const PhotoLoader = {

  accessKey: 'LH6QxsT5fjQWGYWOM4OroLi8FicJsEv-wTHXAksJM_c',
  cachePrefix: 'lal_photo_',
  cacheTTL: 7 * 24 * 60 * 60 * 1000, // 7 дней

  getCached(key) {
    try {
      const raw = localStorage.getItem(this.cachePrefix + key);
      if (!raw) return null;
      const { url, ts } = JSON.parse(raw);
      if (Date.now() - ts > this.cacheTTL) {
        localStorage.removeItem(this.cachePrefix + key);
        return null;
      }
      return url;
    } catch { return null; }
  },

  setCached(key, url) {
    try {
      localStorage.setItem(this.cachePrefix + key, JSON.stringify({ url, ts: Date.now() }));
    } catch {}
  },

  async fetchPhoto(keywords, index) {
    const cacheKey = keywords.replace(/\s+/g, '_') + '_' + index;
    const cached = this.getCached(cacheKey);
    if (cached) return cached;

    try {
      const res = await fetch(
        `https://api.unsplash.com/photos/random?query=${encodeURIComponent(keywords)}&orientation=landscape&client_id=${this.accessKey}`
      );
      if (!res.ok) return null;
      const data = await res.json();
      const url = data.urls.regular;
      this.setCached(cacheKey, url);
      return url;
    } catch { return null; }
  },

  setImg(el, url, alt) {
    const img = document.createElement('img');
    img.alt = alt;
    img.loading = 'lazy';
    img.className = 'auto-photo';
    if (url) {
      img.src = url;
    } else {
      img.style.background = '#f0ece8';
    }
    img.onerror = function() {
      this.onerror = null;
      this.style.background = '#f0ece8';
      this.removeAttribute('src');
    };
    el.prepend(img);
  },

  async init() {
    const photoEls = Array.from(document.querySelectorAll('[data-photo]'));
    const bgEls = Array.from(document.querySelectorAll('[data-photo-bg]'));

    // Загружаем по одному с паузой 200мс — не превышаем лимит
    for (let i = 0; i < photoEls.length; i++) {
      const el = photoEls[i];
      const keywords = el.getAttribute('data-photo');
      const alt = el.getAttribute('data-alt') || keywords;
      const url = await this.fetchPhoto(keywords, i);
      this.setImg(el, url, alt);
      if (url) await new Promise(r => setTimeout(r, 200));
    }

    for (let i = 0; i < bgEls.length; i++) {
      const el = bgEls[i];
      const keywords = el.getAttribute('data-photo-bg');
      const cacheKey = keywords.replace(/\s+/g, '_') + '_bg_' + i;
      const cached = this.getCached(cacheKey);
      let url = cached;
      if (!url) {
        try {
          const res = await fetch(
            `https://api.unsplash.com/photos/random?query=${encodeURIComponent(keywords)}&orientation=landscape&client_id=${this.accessKey}`
          );
          if (res.ok) {
            const data = await res.json();
            url = data.urls.full;
            this.setCached(cacheKey, url);
          }
        } catch {}
      }
      if (url) {
        el.style.backgroundImage = `url('${url}')`;
        el.style.backgroundSize = 'cover';
        el.style.backgroundPosition = 'center';
      }
    }
  }
};

document.addEventListener('DOMContentLoaded', () => PhotoLoader.init());
