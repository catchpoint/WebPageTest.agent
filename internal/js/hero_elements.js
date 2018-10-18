(function(customHeroSelectors) {
/**
Returns an object that can be used to calculate the render times of "hero"
elements. The object will look like:

{
  viewport: { width: Number, height: Number },
  heroes: Array<{name: String, x: Number, y: Number, width: Number, height: Number}>
}
*/

var heroElements = {};
var elementAreas = {};
var vh = document.documentElement.clientHeight;
var vw = document.documentElement.clientWidth;

// Specific elements we look for - headings, images, and anything with the elementtiming attribute
var defaultHeroes = [].slice.call(document.querySelectorAll('h1, h2, img, [elementtiming]'));

defaultHeroes.forEach(function(element) {
  var elementRect = element.getBoundingClientRect();
  var elementArea = visibleElementArea(elementRect);

  if (isVisibleElement(elementRect) && isInViewport(elementRect)) {
    if (element.tagName === 'H1' && isLargestHero('Heading', elementArea)) {
      setHeroElement('Heading', elementRect, elementArea);
    } else if (element.tagName === 'H2' && isLargestHero('Heading2', elementArea)) {
      setHeroElement('Heading2', elementRect, elementArea);
    } else if (element.tagName === 'IMG' && isLargestHero('Image', elementArea)) {
      setHeroElement('Image', elementRect, elementArea);
    }

    // Always record elements with the 'elementtiming' attribute
    if (element.getAttribute('elementtiming')) {
      setHeroElement(element.getAttribute('elementtiming'), elementRect, elementArea);
    }
  }
})

// We also check every other visible element for background images
var docElements = [].slice.call(document.documentElement.getElementsByTagName('*'));

docElements.forEach(function(element) {
  if (hasValidBackgroundImage(element)) {
    var elementRect = element.getBoundingClientRect();
    var elementArea = visibleElementArea(elementRect);

    if (isVisibleElement(elementRect) && isInViewport(elementRect) && isLargestHero('BackgroundImage', elementArea)) {
      setHeroElement('BackgroundImage', elementRect, elementArea);
    }
  }
});

if (heroElements.Heading2) {
  if (!heroElements.Heading) {
    // If there was a H2 but no H1, we use the H2 as the hero heading element
    heroElements.Heading = heroElements.Heading2;
    heroElements.Heading.name = 'Heading';
  }

  // Throw away the H2 data - we only want to use it as a stand-in for H1
  delete heroElements.Heading2;
}

// Look for custom elements. Note that document.querySelector is used (not
// querySelectorAll) to ensure a 1:1 mapping of hero name to element.
if (typeof customHeroSelectors === 'object') {
  for (var heroName in customHeroSelectors) {
    var selector = customHeroSelectors[heroName];
    var element = document.querySelector(selector);

    if (element) {
      var elementRect = element.getBoundingClientRect();
      var elementArea = visibleElementArea(elementRect);

      if (isVisibleElement(elementRect) && isInViewport(elementRect)) {
        setHeroElement(heroName, elementRect, elementArea);
      }
    }
  }
}

return {
  viewport: {
    width: vw,
    height: vh
  },

  // Up until here, heroElements is an object with the hero name as the key. It
  // needs to be converted to an array before we send it back to WPT.
  heroes: Object.keys(heroElements).map(function(k) {
    return heroElements[k];
  })
};

function setHeroElement(name, rect, area) {
  heroElements[name] = {
    name: name,
    x: Math.round(rect.left),
    y: Math.round(rect.top),
    width: Math.round(rect.width),
    height: Math.round(rect.height)
  };

  elementAreas[name] = area;
}

function isLargestHero(name, area) {
  return (
    typeof elementAreas[name] === 'undefined' ||
    elementAreas[name] < area
  );
}

function isVisibleElement(rect) {
  return rect.height > 0;
}

function isInViewport(rect) {
  return !(
    rect.top + rect.height <= 0 || // Element is above the viewport
    rect.top >= vh ||              // Element is below the viewport
    rect.left + rect.width <= 0 || // Element is left of the viewport
    rect.left >= vw                // Element is right of the viewport
  );
}

// Check if an element has a background loaded from a URL
function hasValidBackgroundImage(el) {
  var computedStyle = window.getComputedStyle(el);
  var elementBgImg = computedStyle.backgroundImage.toLowerCase();
  var isRepeating = ['repeat', 'repeat-x', 'repeat-y'].indexOf(computedStyle.backgroundRepeat) !== -1;
  var isCovering = computedStyle.backgroundSize.toLowerCase() === 'cover';

  return (
    elementBgImg.indexOf('url(') === 0 &&
    // We want to ignore repeating patterns, but background-size: cover supersedes this
    (!isRepeating || isCovering)
  );
}

function visibleElementArea(rect) {
  var w = rect.width;
  var h = rect.height;

  if (rect.left < 0) {
    w = w + rect.left;
  } else if (vw < rect.left + rect.width) {
    w = vw - rect.left;
  }

  if (rect.top < 0) {
    h = h + rect.top;
  } else if (vh < rect.top + rect.height) {
    h = vh - rect.top;
  }

  return w * h;
}
})
