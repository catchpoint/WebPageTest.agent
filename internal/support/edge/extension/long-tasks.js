// Monitor for long-tasks (where RAF takes longer than 50ms)
var longTasks = [];
var startTime = performance.now();
var lastTime;

function checkLongTask() {
  var now = performance.now();
  if (lastTime != undefined) {
    var elapsed = now - lastTime;
    if (elapsed > 50) {
      longTasks.push([lastTime, now]);
    }
  }
  lastTime = now;
}

function animationFrame() {
  checkLongTask();
  window.requestAnimationFrame(animationFrame);
}
window.requestAnimationFrame(animationFrame);

// Use PostMessage to trigger the content script to process the interactive periods
// window.postMessage({ wptagent: "GetInteractivePeriods"}, "*");
// document.getElementById('wptagentLongTasks').innerText;
window.addEventListener("message", function(event) {
  if (event.source != window)
    return;

  if (event.data.wptagent && (event.data.wptagent == "GetInteractivePeriods")) {
    checkLongTask();
    // Flip it around and report the interactive periods
    var now = performance.now();
    var interactive = [];
    var start = startTime;
    var count = longTasks.length;
    for (var i = 0; i < count; i++) {
      interactive.push([start, longTasks[i][0]]);
      start = longTasks[i][1];
    }
    interactive.push([start, now]);
    longTasks = [];
    startTime = now;
    // Write it into the DOM of the page so the agent can extract it
    var e = document.getElementById('wptagentLongTasks');
    if (!e && document.body) {
      e = document.createElement('div');
      e.id = 'wptagentLongTasks';
      e.style = 'display: none;';
      document.body.appendChild(e);
    }
    if (e) {
      e.innerHTML = '';
      e.appendChild(document.createTextNode(JSON.stringify(interactive)));
    }
  }
}, false);
