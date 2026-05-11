/**
 * Like a Local — Photo Loader
 * Автоматически загружает фото из Unsplash по ключевым словам.
 * Используется во всех путеводителях.
 *
 * Использование:
 * <div class="photo-card" data-photo="dagestan,sulak,canyon" data-alt="Сулакский каньон"></div>
 *
 * Или для фоновых изображений:
 * <div class="hero" data-photo-bg="dagestan,mountains"></div>
 */

const PhotoLoader = {

  // Базовый URL Unsplash (без API ключа, для статичных сайтов)
  baseUrl: 'https://source.unsplash.com/featured',

  // Кэш загруженных фото чтобы не дублировать запросы
  cache: {},

  /**
   * Генерирует URL фото по ключевым словам
   * @param {string} keywords - через запятую, напр. "dagestan,canyon,sulak"
   * @param {number} width - ширина
   * @param {number} height - высота
   * @param {number} seed - уникальное число чтобы разные карточки получили разные фото
   */
  url(keywords, width = 800, height = 500, seed = 0) {
    // Добавляем seed чтобы разные карточки с похожими тегами получили разные фото
    const cacheBust = seed > 0 ? `&sig=${seed}` : '';
    return `${this.baseUrl}/${width}x${height}/?${encodeURIComponent(keywords)}${cacheBust}`;
  },

  /**
   * Инициализация — находит все элементы с data-photo и data-photo-bg
   * и подставляет фото автоматически
   */
  init() {
    // Карточки с тегом <img> внутри или сам img
    document.querySelectorAll('[data-photo]').forEach((el, index) => {
      const keywords = el.getAttribute('data-photo');
      const alt = el.getAttribute('data-alt') || keywords;
      const width = parseInt(el.getAttribute('data-photo-w') || '800');
      const height = parseInt(el.getAttribute('data-photo-h') || '500');

      const img = document.createElement('img');
      img.src = this.url(keywords, width, height, index + 1);
      img.alt = alt;
      img.loading = 'lazy';
      img.onerror = function() {
        this.onerror = null;
        this.style.background = '#f0ece8';
        this.style.display = 'block';
        this.removeAttribute('src');
      };

      // Применяем класс из атрибута или дефолтный
      const imgClass = el.getAttribute('data-photo-class') || 'auto-photo';
      img.className = imgClass;

      el.prepend(img);
    });

    // Фоновые изображения для hero-секций
    document.querySelectorAll('[data-photo-bg]').forEach((el, index) => {
      const keywords = el.getAttribute('data-photo-bg');
      const width = parseInt(el.getAttribute('data-photo-w') || '1600');
      const height = parseInt(el.getAttribute('data-photo-h') || '900');
      const imgUrl = this.url(keywords, width, height, index + 100);

      el.style.backgroundImage = `url('${imgUrl}')`;
      el.style.backgroundSize = 'cover';
      el.style.backgroundPosition = 'center';
    });
  }
};

// Автозапуск после загрузки DOM
document.addEventListener('DOMContentLoaded', () => PhotoLoader.init());
