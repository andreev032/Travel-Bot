const PHOTO_MAP = {
  // places.html — места
  'sulak,canyon,dagestan,river,turquoise':     'https://images.unsplash.com/photo-1558618666-fcd25c85cd64?auto=format&fit=crop&w=800&q=80',
  'abandoned,village,mountain,caucasus':        'https://images.unsplash.com/photo-1506905925346-21bda4d32df4?auto=format&fit=crop&w=800&q=80',
  'derbent,citadel,ancient,city':              'https://images.unsplash.com/photo-1549989476-b8a6c0a37a34?auto=format&fit=crop&w=800&q=80',
  'sand,dune,desert,russia':                   'https://images.unsplash.com/photo-1509233725247-4a113e562f68?auto=format&fit=crop&w=800&q=80',
  'caspian,sea,beach,dagestan':                'https://images.unsplash.com/photo-1505118380757-91f5f5632de0?auto=format&fit=crop&w=800&q=80',
  'mountain,village,artisan,caucasus':         'https://images.unsplash.com/photo-1464822759023-fed622ff2c3b?auto=format&fit=crop&w=800&q=80',

  // places.html — активности
  'zipline,canyon,adventure,extreme':          'https://images.unsplash.com/photo-1551698618-1dfc2189e823?auto=format&fit=crop&w=800&q=80',
  'rafting,river,mountain,whitewater':         'https://images.unsplash.com/photo-1530866495561-507c9faab2ed?auto=format&fit=crop&w=800&q=80',
  'boat,canyon,river,turquoise':               'https://images.unsplash.com/photo-1544551763-46a013bb70d5?auto=format&fit=crop&w=800&q=80',
  'jeep,offroad,mountain,trail':               'https://images.unsplash.com/photo-1519583272095-6433daf26b6e?auto=format&fit=crop&w=800&q=80',
  'horse,mountain,trail,caucasus':             'https://images.unsplash.com/photo-1534567110353-a3b90e16e9e6?auto=format&fit=crop&w=800&q=80',
  'jump,sea,water,summer':                     'https://images.unsplash.com/photo-1530053969600-caed2596d242?auto=format&fit=crop&w=800&q=80',
  'sup,paddleboard,lake,mountain':             'https://images.unsplash.com/photo-1526080652727-5b77f74eacd2?auto=format&fit=crop&w=800&q=80',

  // food.html — блюда
  'dumplings,meat,caucasus,food':              'https://images.unsplash.com/photo-1563245372-f21724e3856d?auto=format&fit=crop&w=800&q=80',
  'flatbread,stuffed,caucasus,baked':          'https://images.unsplash.com/photo-1574484284002-952d92456975?auto=format&fit=crop&w=800&q=80',
  'nut,butter,paste,healthy':                  'https://images.unsplash.com/photo-1599599761297-a24579d3e32c?auto=format&fit=crop&w=800&q=80',
  'shashlik,lamb,bbq,grill,caucasus':          'https://images.unsplash.com/photo-1544025162-d76694265947?auto=format&fit=crop&w=800&q=80',
  'grilled,trout,river,fish':                  'https://images.unsplash.com/photo-1467003909585-2f8a72700288?auto=format&fit=crop&w=800&q=80',

  // food.html — рестораны
  'restaurant,interior,caucasus,cozy':         'https://images.unsplash.com/photo-1414235077428-338989a2e8c0?auto=format&fit=crop&w=800&q=80',
  'restaurant,loft,bar,modern':               'https://images.unsplash.com/photo-1517248135467-4c7edcad34c4?auto=format&fit=crop&w=800&q=80',
  'restaurant,sea,view,terrace':              'https://images.unsplash.com/photo-1555396273-367ea4eb4db5?auto=format&fit=crop&w=800&q=80',
  'restaurant,local,cozy,traditional':        'https://images.unsplash.com/photo-1537047902294-62a40c20a6ae?auto=format&fit=crop&w=800&q=80',
  'restaurant,terrace,garden,modern':         'https://images.unsplash.com/photo-1504674900247-0877df9cc836?auto=format&fit=crop&w=800&q=80',
  'restaurant,cozy,european,interior':        'https://images.unsplash.com/photo-1559339352-11d035aa65de?auto=format&fit=crop&w=800&q=80',
  'restaurant,view,terrace,historic':         'https://images.unsplash.com/photo-1424847651672-bf20a4b0982b?auto=format&fit=crop&w=800&q=80',
  'cafe,narrow,street,historic':              'https://images.unsplash.com/photo-1521017432531-fbd92d768814?auto=format&fit=crop&w=800&q=80',

  // hotels.html — отели
  'hotel,lobby,modern,russia':                'https://images.unsplash.com/photo-1564501049412-61c2a3083791?auto=format&fit=crop&w=800&q=80',
  'hotel,apartments,caspian':                 'https://images.unsplash.com/photo-1566073771259-6a8506099945?auto=format&fit=crop&w=800&q=80',
  'hotel,room,modern,russia':                 'https://images.unsplash.com/photo-1631049307264-da0ec9d70304?auto=format&fit=crop&w=800&q=80',
  'hotel,luxury,spa,pool':                    'https://images.unsplash.com/photo-1520250497591-112f2f40a3f4?auto=format&fit=crop&w=800&q=80',
  'guesthouse,cozy,villa,garden':             'https://images.unsplash.com/photo-1445019980597-93fa8acb246c?auto=format&fit=crop&w=800&q=80',

  // hotels.html — глэмпинги
  'canyon,river,turquoise,nature':            'https://images.unsplash.com/photo-1504701621678-9e71e39cdacf?auto=format&fit=crop&w=800&q=80',
  'mountain,village,caucasus,house':          'https://images.unsplash.com/photo-1500534314209-a25ddb2bd429?auto=format&fit=crop&w=800&q=80',
  'resort,beach,caspian,sea':                 'https://images.unsplash.com/photo-1507525428034-b723cf961d3e?auto=format&fit=crop&w=800&q=80',
  'glamping,tent,lake,mountains':             'https://images.unsplash.com/photo-1478131143081-80f7f84ca84d?auto=format&fit=crop&w=800&q=80',

  // hero фоны (1600px)
  'dagestan,sulak,canyon,mountains':          'https://images.unsplash.com/photo-1464822759023-fed622ff2c3b?auto=format&fit=crop&w=1600&q=80',
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
      if (url) el.style.backgroundImage = `url('${url}')`;
    });
  }
};

document.addEventListener('DOMContentLoaded', () => PhotoLoader.init());
