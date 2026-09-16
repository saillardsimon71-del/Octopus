import {loadFont} from '@remotion/fonts';
import {staticFile} from 'remotion';

const xbold = loadFont({
  family: 'PoppinsXBold',
  url: staticFile('fonts/Poppins-ExtraBold.ttf'),
  format: 'truetype',
});
const bold = loadFont({
  family: 'PoppinsBold',
  url: staticFile('fonts/Poppins-Bold.ttf'),
  format: 'truetype',
});
const semi = loadFont({
  family: 'PoppinsSemi',
  url: staticFile('fonts/Poppins-SemiBold.ttf'),
  format: 'truetype',
});
const med = loadFont({
  family: 'PoppinsMed',
  url: staticFile('fonts/Poppins-Medium.ttf'),
  format: 'truetype',
});

export const FONT = {
  xbold: xbold.fontFamily,
  bold: bold.fontFamily,
  semi: semi.fontFamily,
  med: med.fontFamily,
};

export const waitFonts = async (): Promise<void> => {
  await Promise.all([
    xbold.waitUntilDone(),
    bold.waitUntilDone(),
    semi.waitUntilDone(),
    med.waitUntilDone(),
  ]);
};
