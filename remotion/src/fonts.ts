import {loadFont} from '@remotion/fonts';
import {staticFile} from 'remotion';

// @remotion/fonts 4.x : loadFont() renvoie Promise<void> et retient lui-meme le rendu
// (delayRender) jusqu'au chargement. L'ancienne version lisait `.fontFamily` sur la promesse
// (undefined) : tout le texte sortait dans la police serif par defaut (audit C6).
const FACES = {
  xbold: {family: 'PoppinsXBold', file: 'Poppins-ExtraBold.ttf'},
  bold: {family: 'PoppinsBold', file: 'Poppins-Bold.ttf'},
  semi: {family: 'PoppinsSemi', file: 'Poppins-SemiBold.ttf'},
  med: {family: 'PoppinsMed', file: 'Poppins-Medium.ttf'},
} as const;

type Face = keyof typeof FACES;

const loading: Promise<void>[] = (Object.keys(FACES) as Face[]).map((k) =>
  loadFont({family: FACES[k].family, url: staticFile(`fonts/${FACES[k].file}`), format: 'truetype'}),
);

const stack = (k: Face) => `'${FACES[k].family}', 'Arial', sans-serif`;

export const FONT: Record<Face, string> = {
  xbold: stack('xbold'),
  bold: stack('bold'),
  semi: stack('semi'),
  med: stack('med'),
};

export const waitFonts = async (): Promise<void> => {
  await Promise.all(loading);
};
