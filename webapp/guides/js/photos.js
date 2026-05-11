/**
 * Like a Local — Photo Loader v2
 * Официальный Unsplash API — фото по ключевым словам.
 */

const PhotoLoader = {

  accessKey: 'LH6QxsT5fjQWGYWOM4OroLi8FicJsEv-wTHXAksJM_c',

  cache: {},

  async fetchPhoto(keywords, w, h, index) {
    const cacheKey = keywords + index;
    if (this.cache[cacheKey]) return this.cache[cacheKey];

    const query = encodeURIComponent(keywords);
    const page = (index % 10) + 1;
    const url = `https://api.unsplash.com/photos/random?query=${query}&orientation=landscape&client_id=${this.accessKey}`;

    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error('API error');
      const data = await res.json();
      const imgUrl = data.urls.regular;
      this.cache[cacheKey] = imgUrl;
      return imgUrl;
    } catch (e) {
      return null;
    }
  },

  async init() {
    const photoEls = document.querySelectorAll('[data-photo]');
    const bgEls = document.querySelectorAll('[data-photo-bg]');

    await Promise.all([
      ...Array.from(photoEls).map(async (el, i) => {
        const keywords = el.getAttribute('data-photo');
        const alt = el.getAttribute('data-alt') || keywords;
        const w = parseInt(el.getAttribute('data-photo-w') || '800');
        const h = parseInt(el.getAttribute('data-photo-h') || '500');

        const imgUrl = await this.fetchPhoto(keywords, w, h, i);

        const img = document.createElement('img');
        img.alt = alt;
        img.loading = 'lazy';
        img.className = 'auto-photo';

        if (imgUrl) {
          img.src = imgUrl;
        } else {
          img.style.background = '#f0ece8';
        }

        img.onerror = function() {
          this.onerror = null;
          this.style.background = '#f0ece8';
          this.removeAttribute('src');
        };

        el.prepend(img);
      }),

      ...Array.from(bgEls).map(async (el, i) => {
        const keywords = el.getAttribute('data-photo-bg');
        const imgUrl = await this.fetchPhoto(keywords, 1600, 900, i + 100);
        if (imgUrl) {
          el.style.backgroundImage = `url('${imgUrl}')`;
          el.style.backgroundSize = 'cover';
          el.style.backgroundPosition = 'center';
        }
      })
    ]);
  }
};

document.addEventListener('DOMContentLoaded', () => PhotoLoader.init());
