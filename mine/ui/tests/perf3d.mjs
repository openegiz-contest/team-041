// Cost of the 3D view in headless Chrome without a graphics card: when the
// first frame of the mine is drawn, frames per second and the cost of a
// frame drawn to the end while the camera turns, and what the scene draws
// while the camera stands still, for each window size.
//
//   node mine/ui/tests/perf3d.mjs [base URL] [sizes, e.g. 1280x720,1920x1080]
//
// Chrome runs with --disable-gpu (see cdp.mjs): on Windows WebGL is then drawn
// by WARP (Microsoft Basic Render Driver), on Linux by SwiftShader. To force
// SwiftShader: CHROME_FLAGS="--use-angle=swiftshader --enable-unsafe-swiftshader".

import { launch, sleep } from './cdp.mjs';

const base = (process.argv[2] ?? 'http://localhost:8088').replace(/\/$/, '');
const sizes = (process.argv[3] ?? '1280x720,1920x1080').split(',').map((size) => size.split('x').map(Number));
// LIGHT_TIME=HH:MM: the scene lit as at that hour (?time=), e.g. at night with its lamps.
const lightTime = /^\d{1,2}:\d{2}$/.test(process.env.LIGHT_TIME ?? '') ? process.env.LIGHT_TIME : null;

for (const [width, height] of sizes) {
  const page = await launch({ width, height });
  try {
    await page.goto(`${base}/${lightTime ? `?time=${lightTime}` : ''}`);
    const first = await page.waitFor('window.minePlan?.scene?.()?.firstFrameAt', 60000, 'the first frame');
    const renderer = await page.evaluate(`(() => {
      const gl = document.createElement('canvas').getContext('webgl2');
      const info = gl && gl.getExtension('WEBGL_debug_renderer_info');
      return gl ? (info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : 'WebGL') : 'no WebGL';
    })()`);
    await sleep(4000);
    const box = await page.evaluate('(() => { const r = document.querySelector("#scene-gl").getBoundingClientRect(); return { x: r.left + r.width * 0.45, y: r.top + r.height * 0.6 }; })()');
    const measure = async (label, preset) => {
      await page.evaluate(`window.minePlan.scene().preset('${preset}')`);
      await sleep(2500);
      await page.evaluate('window.minePlan.scene().setTiming(true); window.minePlan.scene().frameStats()');
      const end = Date.now() + 10000;
      let step = 0;
      await page.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: box.x, y: box.y, button: 'left', clickCount: 1 });
      while (Date.now() < end) {
        step += 1;
        await page.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: box.x + 120 * Math.sin(step / 20), y: box.y + 20 * Math.sin(step / 33), button: 'left', buttons: 1 });
        await sleep(16);
      }
      await page.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: box.x, y: box.y, button: 'left', clickCount: 1 });
      const turning = await page.evaluate('window.minePlan.scene().frameStats()');
      await page.evaluate('window.minePlan.scene().setTiming(false)');
      console.log(`${width}x${height} ${label.padEnd(9)} turning: ${(turning.frames / turning.seconds).toFixed(1)} fps, frame drawn to the end ${turning.avgCostMs.toFixed(1)} ms (worst ${turning.worstCostMs.toFixed(1)}), longest gap ${turning.longestIntervalMs.toFixed(0)} ms, ${turning.triangles} triangles, ${turning.drawCalls} draw calls`);
    };
    console.log(`${width}x${height} renderer: ${renderer}; first frame of the mine ${(first / 1000).toFixed(2)} s after navigation`);
    await measure('overview', 'overview');
    await measure('faces', 'faces');
    await measure('plant', 'plant');
    await page.evaluate('window.minePlan.scene().preset("overview")');
    await sleep(2500);
    await page.evaluate('window.minePlan.scene().frameStats()');
    await sleep(5000);
    const still = await page.evaluate('window.minePlan.scene().frameStats()');
    console.log(`${width}x${height} camera still: ${(still.frames / still.seconds).toFixed(1)} frames per second (moving machines only), ${still.avgCostMs.toFixed(1)} ms of script and drawing per frame`);
  } finally {
    await page.close();
  }
}
