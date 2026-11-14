/** The pictures and videos of the Method page by figure name (`[figure: NAME | caption]` in the text). A name without an entry stays an empty slot. */
export interface Figure {
  kind: 'video' | 'image';
  src: string;
  /** a still shown before the video plays */
  poster?: string;
  alt: string;
  /** who made it and under what terms, shown under the picture when set */
  credit?: string;
}

export const FIGURES: Record<string, Figure> = {
  'raw-exposures': {
    kind: 'video',
    src: '/method/raw-exposures.mp4',
    poster: '/method/raw-exposures.jpg',
    alt: 'Raw SPHEREx exposures of one place played one after another: the pixel grid, the wavelength and the position of the star change from frame to frame',
  },
  'pendulum-barnard': {
    kind: 'video',
    src: '/method/pendulum.mp4',
    poster: '/method/pendulum.jpg',
    alt: "Barnard's Star in the three pass stacks of a field played in the order 1, 2, 3, 2: the stars stand still and Barnard's Star swings",
  },
  'spectral-colour': {
    kind: 'video',
    src: '/method/spectral-colour.mp4',
    poster: '/method/spectral-colour.jpg',
    alt: 'The stack of a pass coloured by wavelength: every exposure is tinted by its own wavelength, a flat spectrum is white',
  },
};
