const PHOTO_MAP = {
  // places.html — места
  'sulak,canyon,dagestan,river,turquoise':     'https://source.unsplash.com/1200x800/?sulak,canyon,dagestan',
  'abandoned,village,mountain,caucasus':        'https://source.unsplash.com/1200x800/?abandoned,village,caucasus,mountains',
  'derbent,citadel,ancient,city':              'https://source.unsplash.com/1200x800/?derbent,fortress,ancient',
  'sand,dune,desert,russia':                   'https://source.unsplash.com/1200x800/?sand,dune,desert,russia',
  'caspian,sea,beach,dagestan':                'https://source.unsplash.com/1200x800/?caspian,sea,beach',
  'mountain,village,artisan,caucasus':         'https://source.unsplash.com/1200x800/?silver,jewelry,handmade',

  // places.html — активности
  'zipline,canyon,adventure,extreme':          'https://source.unsplash.com/1200x800/?zipline,canyon,adventure',
  'rafting,river,mountain,whitewater':         'https://source.unsplash.com/1200x800/?rafting,river,mountain',
  'boat,canyon,river,turquoise':               'https://source.unsplash.com/1200x800/?boat,canyon,river,turquoise',
  'jeep,offroad,mountain,trail':               'https://source.unsplash.com/1200x800/?jeep,offroad,mountain',
  'horse,mountain,trail,caucasus':             'https://source.unsplash.com/1200x800/?horse,mountain,trail',
  'jump,sea,water,summer':                     'https://source.unsplash.com/1200x800/?jump,sea,water,summer',
  'sup,paddleboard,lake,mountain':             'https://source.unsplash.com/1200x800/?paddleboard,lake,mountain',

  // food.html — блюда
  'dumplings,meat,caucasus,food':              'https://source.unsplash.com/1200x800/?caucasus,meat,dumplings,soup',
  'flatbread,stuffed,caucasus,baked':          'https://source.unsplash.com/1200x800/?flatbread,pan,homemade',
  'nut,butter,paste,healthy':                  'https://source.unsplash.com/1200x800/?nut,butter,paste,healthy',
  'shashlik,lamb,bbq,grill,caucasus':          'https://source.unsplash.com/1200x800/?lamb,shashlik,bbq,grill',
  'grilled,trout,river,fish':                  'https://source.unsplash.com/1200x800/?trout,grilled,river',

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
  'dagestan,sulak,canyon,mountains':           'https://source.unsplash.com/1600x900/?dagestan,canyon,turquoise,river',
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
