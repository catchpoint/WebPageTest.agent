(function() {
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
var docElements = [].slice.call(document.documentElement.getElementsByTagName('*'));
var vh = document.documentElement.clientHeight;
var vw = document.documentElement.clientWidth;

docElements.forEach(function(element) {
  var elementRect = element.getBoundingClientRect();
  var elementArea = visibleElementArea(elementRect);

  if (isVisibleElement(element) && isInViewport(elementRect)) {
    // Specific elements we look for - headings and images
    if (element.tagName === 'H1' && isLargestHero('h1', elementArea)) {
      setHeroElement('h1', elementRect, elementArea);
    } else if (element.tagName === 'H2' && isLargestHero('h2', elementArea)) {
      setHeroElement('h2', elementRect, elementArea);
    } else if (element.tagName === 'IMG' && isLargestHero('biggest_img', elementArea)) {
      setHeroElement('biggest_img', elementRect, elementArea);
    }

    // Always check if an element has a background image
    if (hasValidBackgroundImage(element) && elementArea > elementAreas['bg_img']) {
      setHeroElement('bg_img', rect, elementArea);
    }

    // Always record elements with the 'elementtiming' attribute
    if (element.getAttribute('elementtiming')) {
      setHeroElement(element.getAttribute('elementtiming'), rect, elementArea);
    }
  }
});

if (heroElements.h2) {
  if (!heroElements.h1) {
    // If there was an H2 but no H1, we use the H2 as the hero heading element
    heroElements.h1 = heroElements.h2;
    heroElements.h1.name = 'h1';
  }

  // Throw away the H2 data - we only want to use it as a stand-in for H1
  delete heroElements.h2;
}

// Look for custom elements. Note that document.querySelector is used (not
// querySelectorAll) to ensure a 1:1 mapping of hero name to element.
if (typeof window.__wptHeroElements === 'object') {
  for (var heroName in window.__wptHeroElements) {
    var selector = window.__wptHeroElements[heroName];
    var element = document.querySelector(selector);

    if (element) {
      var elementRect = element.getBoundingClientRect();
      var elementArea = visibleElementArea(elementRect);

      if (isVisibleElement(element) && isInViewport(elementRect)) {
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

function isVisibleElement(el) {
  return el.offsetHeight > 0;
}

function isInViewport(rect) {
  return !(
    rect.top + rect.height <= 0 || // Element is above the viewport
    rect.top >= vh ||              // Element is below the viewport
    rect.left + rect.width <= 0 || // Element is left of the viewport
    rect.left >= vw                // Element is right of the viewport
  );
}

// Check if an element has a non-repeating background loaded from a URL
function hasValidBackgroundImage(el) {
  var computedStyle = window.getComputedStyle(el);
  var elementBgImg = computedStyle.backgroundImage.toLowerCase();

  return (
    elementBgImg.indexOf('url(') === 0 &&
    computedStyle.backgroundRepeat !== 'repeat' &&
    computedStyle.backgroundRepeat !== 'repeat-x' &&
    computedStyle.backgroundRepeat !== 'repeat-y'
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
})()
