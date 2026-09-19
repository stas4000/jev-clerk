// Frame-index capture of the film. node capture.cjs spec.cjs frames_out
const path = require('path');
const FRAMES = parseInt(process.env.FILM_FRAMES || '0', 10);
const FROM = parseInt(process.env.FILM_FROM || '0', 10);
module.exports = {
  fps: 30, width: 1920, height: 1080, scale: 1,
  shots: [{
    url: 'file://' + path.resolve(__dirname, 'film.html'),
    ready: page => page.evaluate(() => window.READY),
    get frames() { return FRAMES; },
    drive: (page, i) => page.evaluate(f => window.pose(f), FROM + i),
  }],
};
