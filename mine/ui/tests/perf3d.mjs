// Cost of the 3D view in headless Chrome without a graphics card: when the
// first frame of the mine is drawn, frames per second and the cost of a
// frame drawn to the end while the camera turns (at the points of view
// «Обзор», «Забои», «Фабрика» and «Разрез»), and what the scene draws while
// the camera stands still, for each window size.
//
//   node mine/ui/tests/perf3d.mjs [base URL] [sizes, e.g. 1280x720,1920x1080]
//
// Chrome runs with --disable-gpu (see cdp.mjs): on Windows WebGL is then drawn
// by WARP (Microsoft Basic Render Driver), on Linux by SwiftShader. To force
// SwiftShader: CHROME_FLAGS="--use-angle=swiftshader --enable-unsafe-swiftshader".
// STYLE=realistic (or hologram) measures that look of the 3D view. The
// measurements start 4 s after the first frame, when the Blender models are
// loaded (the line of the renderer says when they were ready).

import { launch, sleep } from './cdp.mjs';

const base = (process.argv[2] ?? 'http://localhost:8088').replace(/\/$/, '');
const sizes = (process.argv[3] ?? '1280x720,1920x1080').split(',').map((size) => size.split('x').map(Number));
// LIGHT_TIME=HH:MM: the scene lit as at that hour (?time=), e.g. at night with its lamps.
const lightTime = /^\d{1,2}:\d{2}$/.test(process.env.LIGHT_TIME ?? '') ? process.env.LIGHT_TIME : null;
// STYLE=hologram|realistic: the look of the 3D view (?style=); the default is the page's own (hologram).
const style = ['hologram', 'realistic'].includes(process.env.STYLE) ? process.env.STYLE : null;
// MODELS=0: the procedural models only (?models=0), to compare with the Blender models.
const query = [lightTime ? `time=${lightTime}` : '', style ? `style=${style}` : '', process.env.MODELS === '0' ? 'models=0' : ''].filter(Boolean).join('&');

for (const [width, height] of sizes) {
  const page = await launch({ width, height });
  try {
    await page.goto(`${base}/${query ? `?${query}` : ''}`);
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
    const look = await page.evaluate('window.minePlan.scene().style');
    const models = await page.evaluate('window.minePlan.scene().models?.() ?? { state: "none" }');
    console.log(`${width}x${height} renderer: ${renderer}; look: ${look}; first frame of the mine ${(first / 1000).toFixed(2)} s after navigation; glTF models ${models.state}${models.loadMs ? ` (loaded and prepared in ${(models.loadMs / 1000).toFixed(2)} s after the first frame)` : ''}`);
    await measure('overview', 'overview');
    await measure('faces', 'faces');
    await measure('plant', 'plant');
    // «Разрез»: the site as a block cut out of the ground (built at the first press; the overview leaves it).
    if (await page.evaluate('window.minePlan.scene().canCutaway')) await measure('cutaway', 'cut');
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
