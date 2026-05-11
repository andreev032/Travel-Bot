# Photo Loader — Как использовать в новых путеводителях

## Подключение
В каждом HTML файле путеводителя добавить в <head>:
<script src="../js/photos.js" defer></script>

## Карточка места с автофото
<div class="place-card">
  <div data-photo="paris,eiffel,tower,france" data-alt="Эйфелева башня"></div>
  <div class="place-content">
    <h3>Эйфелева башня</h3>
    ...
  </div>
</div>

## Hero с фоновым фото
<section class="hero" data-photo-bg="paris,france,city,night">

## Ключевые слова — правила
- Писать на английском
- 3-5 слов через запятую
- Конкретнее = точнее фото: "sulak canyon dagestan" лучше чем "nature"
- Для стран: "{country},{city},{landmark}" напр. "japan,tokyo,temple"
- Для еды: "{dish},{ingredient},{style}" напр. "sushi,japan,seafood"
- Для отелей: "{type},{feature},{location}" напр. "resort,pool,tropical"

## Источник фото
Unsplash — бесплатно, коммерческое использование разрешено.
URL: https://source.unsplash.com/featured/{w}x{h}/?{keywords}
