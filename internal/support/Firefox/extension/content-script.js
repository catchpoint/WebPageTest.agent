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

function wptagentGetInteractivePeriods() {
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
  return JSON.stringify(interactive);
}

exportFunction(wptagentGetInteractivePeriods, window, {defineAs:'wptagentGetInteractivePeriods'});
