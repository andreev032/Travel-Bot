/* Shared persistence for personal Mini Apps. Legacy keys remain untouched. */
(async () => {
  const script = document.getElementById('personal-app');
  const kind = script.dataset.kind;
  const legacyKey = script.dataset.key;
  const banner = document.createElement('div');
  banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:100000;background:#182333;color:white;padding:12px;font:14px sans-serif';
  const label = document.createElement('span');
  const retry = document.createElement('button');
  retry.textContent = 'Повторить';
  banner.append(label, retry);
  document.body.append(banner);
  document.body.style.paddingTop = '60px';
  let revision, data, pending, timer, sending = false, draftKey, durable = true;
  const show = text => { label.textContent = text + (durable ? ' ' : ' Локальная копия не записана. Не закрывай окно до подтверждения сервера. '); };
  show('Загрузка сохранённых данных…');
  const api = new URLSearchParams(location.hash.slice(1)).get('api');
  let origin;
  try {
    origin = new URL(api);
    if (origin.protocol !== 'https:' || !origin.hostname.endsWith('.up.railway.app') ||
        origin.username || origin.password) throw Error('origin');
  } catch (_) {
    show('Открой раздел заново через меню бота. Старые локальные записи сохранены.');
    retry.onclick = () => location.reload();
    return;
  }
  const initData = window.Telegram?.WebApp?.initData;
  if (!initData) {
    show('Для сохранения открой раздел кнопкой «Открыть» в чате бота.');
    return;
  }
  async function request(body) {
    const response = await fetch(origin.origin + '/api/records/' + kind, {
      method: body ? 'POST' : 'GET',
      headers: {'Content-Type': 'application/json', 'X-Telegram-Init-Data': initData},
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(15000)
    });
    if (!response.ok) {
      const error = new Error('request');
      error.status = response.status;
      throw error;
    }
    return response.json();
  }
  function keepDraft() {
    try { localStorage.setItem(draftKey, JSON.stringify({revision, data, pending})); durable = true; }
    catch (_) { durable = false; }
  }
  async function flush() {
    if (sending) return;
    sending = true;
    try {
      while (pending) {
        const operation = pending;
        const result = await request(operation);
        revision = result.revision;
        if (JSON.stringify(data) === JSON.stringify(operation.data)) {
          pending = null;
          localStorage.removeItem(draftKey);
          show('Сохранено на сервере');
        } else {
          pending = {revision, data: JSON.parse(JSON.stringify(data)), request_id: crypto.randomUUID()};
          keepDraft();
          await new Promise(resolve => setTimeout(resolve, 1100));
        }
      }
    } catch (error) {
      keepDraft();
      show(error.status === 409
        ? 'Конфликт с другим устройством. Черновик сохранён локально. Открой раздел заново.'
        : error.status === 413 ? 'Слишком много данных для сервера. Локальная копия сохранена.'
        : 'На сервере не подтверждено. Повтори сохранение; при истечении сессии открой раздел заново.');
    } finally { sending = false; }
  }
  retry.onclick = flush;
  try {
    const snapshot = await request();
    revision = snapshot.revision;
    data = snapshot.data;
    draftKey = 'cloud_draft_' + snapshot.user + '_' + kind;
    let draft;
    try { draft = JSON.parse(localStorage.getItem(draftKey)); } catch (_) {}
    if (draft) {
      // Never silently discard a conflicting draft or overwrite the server.
      if (draft.revision !== revision) {
        show('Есть локальный черновик другой версии. Скачай его перед открытием серверных данных.');
        retry.textContent = 'Скачать черновик и открыть';
        retry.onclick = () => {
          const link = document.createElement('a');
          link.href = URL.createObjectURL(new Blob([JSON.stringify(draft)], {type:'application/json'}));
          link.download = kind + '-draft.json';
          link.click();
          localStorage.setItem(draftKey + '_archive_' + Date.now(), JSON.stringify(draft));
          localStorage.removeItem(draftKey);
          location.reload();
        };
        return;
      }
      data = draft.data;
      pending = draft.pending;
    } else if (data === null) {
      const old = localStorage.getItem(legacyKey);
      if (old && confirm('На устройстве есть старые записи. Импортировать их в этот Telegram-аккаунт?')) {
        data = JSON.parse(old);
        pending = {revision, data, request_id: crypto.randomUUID()};
        keepDraft();
      }
    }
    window.personalRecords = {
      flush: async () => { await flush(); return !pending; },
      read: () => data === null ? null : JSON.stringify(data),
      write: value => {
        data = JSON.parse(value);
        if (!pending || !sending) pending = {revision, data: JSON.parse(value), request_id: crypto.randomUUID()};
        keepDraft();
        show('Изменения ещё не подтверждены сервером');
        clearTimeout(timer);
        timer = setTimeout(flush, 1500);
      }
    };
    const active = document.createElement('script');
    active.textContent = script.textContent;
    document.body.append(active);
    show(pending ? 'Есть несохранённый черновик. Нажми «Повторить».' : 'Загружено с сервера');
  } catch (_) {
    show('Не удалось загрузить данные. Редактирование не запущено. Повтори открытие.');
    retry.onclick = () => location.reload();
  }
})();
