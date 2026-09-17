import {Config} from '@remotion/cli/config';

Config.setVideoImageFormat('jpeg');
Config.setOverwriteOutput(true);

// Cross-platform: on Windows we may provide Edge/Chrome explicitly, while the
// cloud worker lets Remotion discover the Chromium executable installed in the image.
const browserExecutable = process.env.PODALUX_REMOTION_BROWSER?.trim();
if (browserExecutable) {
  Config.setBrowserExecutable(browserExecutable);
}
