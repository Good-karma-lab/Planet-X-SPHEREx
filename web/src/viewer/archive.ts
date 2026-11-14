// Archive images of a field: cutouts of older all-sky surveys at the field's place and size, north up and east left like the
// SPHEREx picture, so that a star's motion over decades shows when they are blinked with the SPHEREx seasons.

import { cutout, tiles } from '../lib/hips';

export interface ArchiveSurvey {
  id: string;
  name: string;
  /** the years the survey's images of the sky were taken (a HiPS carries no per-place date) */
  years: string;
  /** the middle of those years, for placing a moving star's marker */
  epoch: number;
  maxOrder: number;
}

export const ARCHIVE: ArchiveSurvey[] = [
  { id: 'dss2red', name: 'DSS2 red', years: '1985–1999', epoch: 1992.5, maxOrder: 9 },
  { id: '2massk', name: '2MASS Ks', years: '1997–2001', epoch: 1999.0, maxOrder: 9 },
  { id: 'wise1', name: 'AllWISE W1', years: '2010', epoch: 2010.3, maxOrder: 8 },
];

/** The cutouts of every archive survey for one field; `onChange` runs when a cutout gets sharper as tiles arrive. */
export class ArchiveSet {
  readonly canvases = new Map<string, HTMLCanvasElement>();
  readonly complete = new Set<string>();
  private off: () => void;
  private timer = 0;
  private stopped = false;

  constructor(
    readonly ra: number,
    readonly dec: number,
    readonly fovArcmin: number,
    readonly size: number,
    private onChange: () => void,
  ) {
    for (const s of ARCHIVE) {
      const c = document.createElement('canvas');
      c.width = c.height = size;
      this.canvases.set(s.id, c);
    }
    this.off = tiles.onTile(() => this.schedule());
    this.render();
  }

  private schedule(): void {
    if (this.timer || this.stopped) return;
    this.timer = window.setTimeout(() => {
      this.timer = 0;
      this.render();
    }, 250);
  }

  private render(): void {
    if (this.stopped) return;
    for (const s of ARCHIVE) {
      if (this.complete.has(s.id)) continue;
      const { data, complete } = cutout(s.id, s.maxOrder, this.ra, this.dec, this.fovArcmin / 60, this.size);
      this.canvases.get(s.id)!.getContext('2d')!.putImageData(data, 0, 0);
      if (complete) this.complete.add(s.id);
    }
    this.onChange();
  }

  dispose(): void {
    this.stopped = true;
    clearTimeout(this.timer);
    this.off();
  }
}
