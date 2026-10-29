// Constantes del sitio. Un solo lugar para URLs y textos que se repiten.
export const REPO = 'https://github.com/danpv95/voz';
export const ISSUE_ONE_LINE = `${REPO}/issues/12`;
export const CHANGELOG_URL = `${REPO}/blob/main/CHANGELOG.md`;
export const LICENSE_URL = `${REPO}/blob/main/LICENSE`;
export const NAME = 'voz';
export const TAGLINE = 'Dicta como hablas. Pega como escribes.';
export const DESCRIPTION =
  'Dictado por voz para la terminal, 100 % en tu Mac. Hablas, voz lo pasa a texto con Whisper y lo deja en el portapapeles.';

export const tagUrl = (version: string): string => `${REPO}/releases/tag/v${version}`;
export const guideUrl = (version: string): string =>
  `${REPO}/releases/download/v${version}/guia-voz.pdf`;
