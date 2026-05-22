const PHOTO_MAP = {
  // places.html — места
  'sulak,canyon,dagestan,river,turquoise':     'https://blog.ostrovok.ru/wp-content/uploads/2023/11/1%D0%BA%D0%BE%D0%BF%D0%B8%D1%8F-10.jpg',
  'abandoned,village,mountain,caucasus':        'https://images.unsplash.com/photo-1673446319197-35ba29be3d22?auto=format&fit=crop&w=1200&q=80',
  'derbent,citadel,ancient,city':              'https://images.unsplash.com/photo-1667910548146-c1f350f87ed9?auto=format&fit=crop&w=1200&q=80',
  'sand,dune,desert,russia':                   'https://blog.ostrovok.ru/wp-content/uploads/2023/11/11%D0%BA%D0%BE%D0%BF%D0%B8%D1%8F-1-2.jpg',
  'caspian,sea,beach,dagestan':                'https://images.unsplash.com/photo-1665660763290-2e5c5949e06f?auto=format&fit=crop&w=1200&q=80',
  'mountain,village,artisan,caucasus':         'https://images.unsplash.com/photo-1609619742069-f5e18afeef17?auto=format&fit=crop&w=1200&q=80',

  // places.html — активности
  'zipline,canyon,adventure,extreme':          'https://images.unsplash.com/photo-1771583102717-b0bd89c34979?auto=format&fit=crop&w=1200&q=80',
  'rafting,river,mountain,whitewater':         'https://images.unsplash.com/photo-1732792184423-b33f1e78ff18?auto=format&fit=crop&w=1200&q=80',
  'boat,canyon,river,turquoise':               'https://images.unsplash.com/photo-1776520037771-54bf7c3b5d16?auto=format&fit=crop&w=1200&q=80',
  'jeep,offroad,mountain,trail':               'https://images.unsplash.com/photo-1626114918166-cd3ec13bd4f0?auto=format&fit=crop&w=1200&q=80',
  'horse,mountain,trail,caucasus':             'https://images.unsplash.com/photo-1761760554757-3e9f4f427447?auto=format&fit=crop&w=1200&q=80',
  'jump,sea,water,summer':                     'https://source.unsplash.com/1200x800/?jump,sea,water,summer',
  'sup,paddleboard,lake,mountain':             'https://images.unsplash.com/photo-1595207581431-40fbad5ec0d7?auto=format&fit=crop&w=1200&q=80',

  // food.html — блюда
  'dumplings,meat,caucasus,food':              'https://source.unsplash.com/1200x800/?caucasus,meat,dumplings,soup',
  'flatbread,stuffed,caucasus,baked':          'https://source.unsplash.com/1200x800/?flatbread,pan,homemade',
  'nut,butter,paste,healthy':                  'https://source.unsplash.com/1200x800/?nut,butter,paste,healthy',
  'shashlik,lamb,bbq,grill,caucasus':          'https://source.unsplash.com/1200x800/?lamb,shashlik,bbq,grill',
  'grilled,trout,river,fish':                  'https://blog.ostrovok.ru/wp-content/uploads/2023/11/10%D0%BA%D0%BE%D0%BF%D0%B8%D1%8F-6.jpg',

  // food.html — рестораны
  'restaurant,interior,caucasus,cozy':         'https://source.unsplash.com/1200x800/?restaurant,interior,cozy',
  'restaurant,loft,bar,modern':                'https://source.unsplash.com/1200x800/?restaurant,loft,bar,modern',
  'restaurant,sea,view,terrace':               'https://source.unsplash.com/1200x800/?restaurant,sea,view,terrace',
  'restaurant,local,cozy,traditional':         'https://source.unsplash.com/1200x800/?restaurant,local,traditional',
  'restaurant,terrace,garden,modern':          'https://source.unsplash.com/1200x800/?restaurant,terrace,garden',
  'restaurant,cozy,european,interior':         'https://source.unsplash.com/1200x800/?restaurant,cozy,european',
  'restaurant,view,terrace,historic':          'https://source.unsplash.com/1200x800/?restaurant,view,terrace,historic',
  'cafe,narrow,street,historic':               'https://source.unsplash.com/1200x800/?cafe,narrow,street,historic',

  // hotels.html — отели
  'hotel,lobby,modern,russia':                 'https://source.unsplash.com/1200x800/?hotel,lobby,modern',
  'hotel,apartments,caspian':                  'https://source.unsplash.com/1200x800/?hotel,apartments',
  'hotel,room,modern,russia':                  'https://source.unsplash.com/1200x800/?hotel,room,modern',
  'hotel,luxury,spa,pool':                     'https://source.unsplash.com/1200x800/?hotel,luxury,spa,pool',
  'guesthouse,cozy,villa,garden':              'https://source.unsplash.com/1200x800/?guesthouse,cozy,villa',

  // hotels.html — глэмпинги
  'canyon,river,turquoise,nature':             'https://source.unsplash.com/1200x800/?glamping,mountains,russia',
  'mountain,village,caucasus,house':           'https://source.unsplash.com/1200x800/?glamping,mountains,russia',
  'resort,beach,caspian,sea':                  'https://source.unsplash.com/1200x800/?beach,resort,sea',
  'glamping,tent,lake,mountains':              'https://source.unsplash.com/1200x800/?glamping,tent,mountains',

  // hero фоны (1600px)
  'dagestan,sulak,canyon,mountains':           'https://blog.ostrovok.ru/wp-content/uploads/2023/11/%D0%9E%D0%B1%D0%BB%D0%BE%D0%B6%D0%BA%D0%B0-4.png',
};

const PhotoLoader = {
  init() {
    document.querySelectorAll('[data-photo]').forEach(el => {
      const key = el.dataset.photo;
      const url = PHOTO_MAP[key];
      const img = document.createElement('img');
      img.src = url || 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
      img.alt = el.dataset.alt || '';
      img.loading = 'lazy';
      img.className = 'auto-photo';
      img.onerror = function() { this.onerror=null; this.style.background='#ededed'; this.removeAttribute('src'); };
      el.prepend(img);
    });

    document.querySelectorAll('[data-photo-bg]').forEach(el => {
      const key = el.dataset.photoBg;
      const url = PHOTO_MAP[key];
      if (url) {
        el.style.backgroundImage = `url('${url}')`;
        el.style.backgroundSize = 'cover';
        el.style.backgroundPosition = 'center';
      }
    });
  }
};

document.addEventListener('DOMContentLoaded', () => PhotoLoader.init());
