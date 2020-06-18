// Monitor for long-tasks (where RAF takes longer than 50ms)
let lastTime;

function wptAnimationFrame() {
  let now = performance.now();
  if (lastTime != undefined) {
    let elapsed = now - lastTime;
    if (elapsed > 50) {
      browser.runtime.sendMessage({msg:'longTask', dur:elapsed})
    }
  }
  lastTime = now;
  window.requestAnimationFrame(wptAnimationFrame);
}
window.requestAnimationFrame(wptAnimationFrame);
