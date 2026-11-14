// The fifteen parts of the Method page: their group, title, lead and the sub-headings of the contents column. The text of each part is in ./text/NN-*.md.

export interface PartMeta {
  id: string;
  group: 'Story' | 'Methods' | 'Results';
  title: string;
  lead: string;
  subs: string[];
}

export const PART_META: PartMeta[] = [
  {
    "id": "01",
    "group": "Story",
    "title": "The challenge",
    "lead": "NASA asks for a public web tool that shows SPHEREx images of any patch of sky like a little video, so that anyone can see the sky change and spot what moves. We read it as: people find the movers by eye, and the program makes that easy and checks what they find.",
    "subs": [
      "The task in NASA’s words",
      "What we read it to mean",
      "How this page is organised"
    ]
  },
  {
    "id": "02",
    "group": "Story",
    "title": "The telescope",
    "lead": "SPHEREx photographs the whole sky every six months in 102 colours of infrared light, from 0.75 to 5 µm. Each exposure sees one colour at each place on the sky; the colours come from moving the sky across the filter.",
    "subs": [
      "Six detectors, 102 colours",
      "Passes every six months",
      "The archive and its releases"
    ]
  },
  {
    "id": "03",
    "group": "Story",
    "title": "How Pluto was found",
    "lead": "In 1930 Clyde Tombaugh compared two photographs of the same stars taken days apart, flipping between them. The stars stood still; one faint dot jumped. That dot was Pluto. We do the same with SPHEREx, at three time scales.",
    "subs": [
      "The blink comparator",
      "Why a mover jumps",
      "Our blink: years, days, hours"
    ]
  },
  {
    "id": "04",
    "group": "Story",
    "title": "The search for Planet Nine",
    "lead": "The farthest bodies beyond Neptune share a strange alignment. A planet five to ten times the Earth’s mass, hundreds of au out, would explain it. Nobody has found it yet: it is faint, slow, and somewhere along a long band of sky.",
    "subs": [
      "The clustered far bodies",
      "The predicted planet",
      "Searches so far",
      "What SPHEREx adds"
    ]
  },
  {
    "id": "05",
    "group": "Story",
    "title": "From raw data to a video",
    "lead": "A SPHEREx file is one detector’s picture of one pointing, in one colour per place. We read only the pixels a field needs, clean them, stack the exposures of each pass, colour them by their own spectrum and play the passes as a film.",
    "subs": [
      "A Level-2 exposure",
      "Reading only what a field needs",
      "Stacks per pass",
      "Colour and the motion layer"
    ]
  },
  {
    "id": "06",
    "group": "Story",
    "title": "What we do and why",
    "lead": "Three time scales, six searches, a spectrum for any dot, an anomaly map and Find Planet X. Each answers one question a person brings to the sky; each find is a lead until another telescope agrees.",
    "subs": [
      "Three time scales",
      "Six searches",
      "A lead is not a discovery"
    ]
  },
  {
    "id": "07",
    "group": "Methods",
    "title": "Data and reading",
    "lead": "Level-2 files, read by byte range: only the rows a field covers. Every pixel gets its own wavelength from the spectral WCS; flags mark what to ignore.",
    "subs": [
      "Level-2 layout",
      "Range reads",
      "Flags",
      "Wavelength of every pixel",
      "The exposure index"
    ]
  },
  {
    "id": "08",
    "group": "Methods",
    "title": "Cleaning and stacks",
    "lead": "Background, masks, cosmic rays left out one exposure at a time, streaks found per visit, then one stack per pass and detector.",
    "subs": [
      "Background and masks",
      "The stack and its kernel",
      "Cosmic rays and streaks",
      "Saturation and the white frame"
    ]
  },
  {
    "id": "09",
    "group": "Methods",
    "title": "Views",
    "lead": "One display law for every picture; colour from the source’s own spectrum; named bands; the pendulum, the walls, the difference and the films.",
    "subs": [
      "Display law",
      "Spectral colour and named bands",
      "Pendulum and walls",
      "Difference and speed rings",
      "Films of a pass and of a visit"
    ]
  },
  {
    "id": "10",
    "group": "Methods",
    "title": "Spectra",
    "lead": "PSF photometry in every exposure that covers a source, one point per exposure, measured live. Averaged under the 2MASS and WISE filters, it matches the catalogues.",
    "subs": [
      "Photometry per exposure",
      "Saturated sources",
      "Catalogue check",
      "Moving bodies"
    ]
  },
  {
    "id": "11",
    "group": "Methods",
    "title": "Searches",
    "lead": "Three motion scales: between passes (years), inside a pass (days), inside a visit (hours). Beyond Neptune, Find Planet X adds the exposures along every orbit a body could have.",
    "subs": [
      "Between passes",
      "Pairs",
      "Inside a pass: the film search",
      "Inside a visit",
      "Beyond Neptune"
    ]
  },
  {
    "id": "12",
    "group": "Methods",
    "title": "Anomalies, identification and chance",
    "lead": "The field’s ordinary star is built from its own spectra; what does not fit is flagged. SIMBAD and Gaia name what is known; every search runs again on shuffled data.",
    "subs": [
      "The anomaly map",
      "SIMBAD and Gaia context",
      "Brown dwarf candidates",
      "Chance controls"
    ]
  },
  {
    "id": "13",
    "group": "Methods",
    "title": "Cross-checks and exports",
    "lead": "Catalogue cross-matches, deep optical references, the minor-planet checker and an ADES file for any mover lead.",
    "subs": [
      "Catalogue cross-match",
      "Deep optical references",
      "ADES export"
    ]
  },
  {
    "id": "14",
    "group": "Results",
    "title": "Validation: how we know it works",
    "lead": "Every claim is checked against another telescope or catalogue: ESA Gaia, SIMBAD, JPL, NEOWISE, 2MASS and WISE. The numbers below are what spherex.space answers today.",
    "subs": [
      "Eight checks",
      "The table",
      "Dated measurements"
    ]
  },
  {
    "id": "15",
    "group": "Results",
    "title": "What we state and what we do not",
    "lead": "Every search has a depth; below it, finding nothing proves nothing. A lead is not a discovery, and no search here claims Planet X.",
    "subs": [
      "Depth of each search",
      "What a lead is",
      "What we do not claim"
    ]
  }
];
