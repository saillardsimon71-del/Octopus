import React, {useEffect} from 'react';
import {
  AbsoluteFill,
  Img,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
  delayRender,
  continueRender,
} from 'remotion';
import {CAPTIONS} from './data/captions';
import {JOB} from './data/job';
import {FONT, waitFonts} from './fonts';

const P = JOB.palette as Record<string, string>;

type Accent = {emoji: string; text: string; bg: string};
type Meta = {label: string; icon: string; img: string; accent: Accent; full: boolean};

// Libelles propres a l'offre : fournis par le job (champ `visuel`), sinon ceux de l'offre Impayes
// d'origine. Avant, ils etaient en dur : la video Devis/CGV affichait "FACTURE IMPAYEE" (audit C6).
type RoleText = {label?: string; icon?: string; accent_emoji?: string; accent_text?: string; img?: string};
type Visuel = {
  hook?: RoleText;
  douleur?: RoleText;
  preuve?: RoleText & {card_title?: string; card_rows?: [string, string][]};
  soulagement?: RoleText & {banner?: string};
  cta?: RoleText;
};
const DEFAULT_VISUEL: Required<Visuel> = {
  hook: {label: 'FACTURE IMPAYÉE', icon: '📄', accent_emoji: '⏰', accent_text: '30 JOURS'},
  douleur: {label: 'LE CASH QUI NE RENTRE PAS', icon: '💸', accent_emoji: '💸', accent_text: 'CASH BLOQUÉ'},
  preuve: {
    label: 'LA RELANCE PRO', icon: '✅', accent_emoji: '📄', accent_text: 'RELANCE N°2',
    card_title: 'RELANCE N°2',
    card_rows: [['Facture #2026-041', '1 240 €'], ['Statut', 'ENCAISSÉ +1 240 € ✓']],
  },
  soulagement: {label: 'LE CONTRÔLE', icon: '🤝', accent_emoji: '✓', accent_text: 'PAYÉE', banner: '+1 240 € ENCAISSÉ'},
  cta: {label: 'À VOUS DE JOUER', icon: '👇', accent_emoji: '👇'},
};
const JOB_VISUEL = ((JOB as unknown as {visuel?: Visuel | null}).visuel ?? {}) as Visuel;
const V = {
  hook: {...DEFAULT_VISUEL.hook, ...JOB_VISUEL.hook},
  douleur: {...DEFAULT_VISUEL.douleur, ...JOB_VISUEL.douleur},
  preuve: {...DEFAULT_VISUEL.preuve, ...JOB_VISUEL.preuve},
  soulagement: {...DEFAULT_VISUEL.soulagement, ...JOB_VISUEL.soulagement},
  cta: {...DEFAULT_VISUEL.cta, ...JOB_VISUEL.cta},
};
const CARD_ROWS = V.preuve.card_rows ?? [];

const meta = (role: keyof Visuel, img: string, full: boolean, bg: string, accentText?: string): Meta => ({
  label: V[role].label ?? '',
  icon: V[role].icon ?? '',
  img,
  full,
  accent: {emoji: V[role].accent_emoji ?? '', text: accentText ?? V[role].accent_text ?? '', bg},
});

// `img` vient du job quand tools/fetch_broll.py a trouve une image libre pour l'offre ;
// sinon on garde les photos livrees avec le depot.
const SEG: Record<string, Meta> = {
  hook: meta('hook', V.hook.img ?? 'human.jpg', true, '#d90429'),
  douleur: meta('douleur', V.douleur.img ?? 'human2.jpg', false, '#b91c1c'),
  preuve: meta('preuve', V.preuve.img ?? 'human.jpg', false, '#b45309'),
  soulagement: meta('soulagement', V.soulagement.img ?? 'face.jpg', true, '#15803d'),
  cta: meta('cta', V.cta.img ?? 'face2.jpg', true, '#ea580c', JOB.prix as string),
};

const norm = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9àâçéèêëîôûùüÿñœ'-]/g, '');
const KEY = new Set((JOB.keywords as readonly string[]).map(norm));

type Seg = {seg: number; role: string; words: typeof CAPTIONS; start: number; end: number};
const SEGS: Seg[] = [];
for (const w of CAPTIONS) {
  const last = SEGS[SEGS.length - 1];
  if (!last || last.seg !== w.seg) {
    SEGS.push({seg: w.seg, role: w.role, words: [w], start: w.start, end: w.end});
  } else {
    last.words.push(w);
    last.end = w.end;
  }
}

const FLOATERS = ['💸', '⏱️', '📄', '✅', '🤝'];

const TINT: Record<string, string> = {
  hook: 'rgba(217,4,41,0.12)',
  douleur: 'rgba(185,28,28,0.14)',
  preuve: 'rgba(180,83,9,0.12)',
  soulagement: 'rgba(21,128,61,0.14)',
  cta: 'rgba(234,88,12,0.12)',
};

export const CashShort: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const t = frame / fps;

  useEffect(() => {
    const h = delayRender('fonts');
    waitFonts().then(() => continueRender(h)).catch(() => continueRender(h));
    return () => continueRender(h);
  }, []);

  let segIdx = SEGS.length - 1;
  for (let i = 0; i < SEGS.length; i++) {
    if (t < SEGS[i].end) {
      segIdx = i;
      break;
    }
  }
  const seg = SEGS[segIdx];
  const meta = SEG[seg.role] ?? SEG.hook;
  const accent = meta.accent;
  const tint = TINT[seg.role] ?? 'rgba(0,0,0,0)';
  const full = meta.full ?? false;
  const wordColor = full ? P.highlight : P.ink;

  const segIn = interpolate(t, [seg.start, seg.start + 0.25], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  const segProg = interpolate(t, [seg.start, seg.end], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const zoom = interpolate(segProg, [0, 1], [1.0, 1.22]);
  const panX = interpolate(segProg, [0, 1], [-2, 2]) * (segIdx % 2 === 0 ? 1 : -1);

  const maxChars = Math.max(...seg.words.map((w) => w.text.length));
  const wordFont = Math.max(66, Math.min(92, Math.round(920 / (maxChars * 0.6))));

  const isCta = seg.role === 'cta';
  const isPreuve = seg.role === 'preuve';
  const isSoulagement = seg.role === 'soulagement';
  const ctaIn = interpolate(t, [seg.start, seg.start + 0.3], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const progress = frame / durationInFrames;

  return (
    <AbsoluteFill style={{backgroundColor: P.bgBottom, overflow: 'hidden'}}>
      <AbsoluteFill style={{transform: `scale(${zoom}) translateX(${panX}%)`, transformOrigin: '50% 42%'}}>
        <div
          style={{
            position: 'absolute',
            inset: 0,
            background: `linear-gradient(160deg, ${P.bgTop} 0%, ${P.bgMid} 45%, ${P.bgBottom} 100%)`,
          }}
        />
        <div style={{position: 'absolute', width: 900, height: 900, borderRadius: '50%', left: -300, top: -200, background: 'rgba(255,235,180,0.35)', transform: `translate(${interpolate(frame, [0, durationInFrames], [0, 90])}px, ${interpolate(frame, [0, durationInFrames], [0, 70])}px)`}} />
        <div style={{position: 'absolute', width: 700, height: 700, borderRadius: '50%', right: -250, bottom: 300, background: 'rgba(255,150,40,0.30)', transform: `translate(${interpolate(frame, [0, durationInFrames], [0, -80])}px, ${interpolate(frame, [0, durationInFrames], [0, -60])}px)`}} />
      </AbsoluteFill>

      {FLOATERS.map((f, i) => (
        <div
          key={i}
          style={{
            position: 'absolute',
            left: 90 + i * 225,
            top: interpolate(frame, [0, durationInFrames], [1700 - i * 160, 820 - i * 50]),
            fontSize: 62,
            opacity: 0.20,
            transform: `rotate(${Math.sin(frame / 40 + i) * 15}deg)`,
          }}
        >
          {f}
        </div>
      ))}

      {full ? (
        <AbsoluteFill>
          <Img
            src={staticFile(`img/${meta.img}`)}
            style={{width: '100%', height: '100%', objectFit: 'cover', transform: `scale(${zoom * (0.96 + 0.04 * segIn)})`}}
          />
          <div style={{position: 'absolute', inset: 0, background: 'linear-gradient(180deg, rgba(43,18,6,0.22) 0%, rgba(43,18,6,0.06) 45%, rgba(43,18,6,0.48) 100%)'}} />
          <div style={{position: 'absolute', top: 90, right: 50, display: 'flex', alignItems: 'center', gap: 12, padding: '14px 26px', borderRadius: 24, backgroundColor: accent.bg, color: '#fff', fontFamily: FONT.xbold, fontSize: 44, boxShadow: '0 10px 26px rgba(0,0,0,0.35)', transform: `rotate(${Math.sin(frame / 12) * 2}deg)`}}>
            <span style={{fontSize: 46}}>{accent.emoji}</span>
            <span>{accent.text}</span>
          </div>
        </AbsoluteFill>
      ) : (
        <AbsoluteFill style={{top: 100, left: 70, width: 940, height: 430, opacity: segIn, transform: `scale(${zoom * (0.95 + 0.05 * segIn)})`, transformOrigin: '50% 50%'}}>
          <Img src={staticFile(`img/${meta.img}`)} style={{width: '100%', height: '100%', objectFit: 'cover', borderRadius: 44, boxShadow: '0 24px 60px rgba(60,20,0,0.35)'}} />
          <div style={{position: 'absolute', inset: 0, borderRadius: 44, background: 'linear-gradient(180deg, rgba(43,18,6,0.02) 0%, rgba(43,18,6,0.4) 100%)'}} />
          <div style={{position: 'absolute', top: 26, right: 26, display: 'flex', alignItems: 'center', gap: 12, padding: '14px 26px', borderRadius: 24, backgroundColor: accent.bg, color: '#fff', fontFamily: FONT.xbold, fontSize: 40, boxShadow: '0 10px 26px rgba(0,0,0,0.3)', transform: `rotate(${Math.sin(frame / 12) * 2}deg) scale(${0.9 + 0.1 * segIn})`}}>
            <span style={{fontSize: 42}}>{accent.emoji}</span>
            <span>{accent.text}</span>
          </div>
        </AbsoluteFill>
      )}

      <AbsoluteFill style={{top: 545, alignItems: 'center'}}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 16,
            padding: '16px 34px',
            borderRadius: 999,
            backgroundColor: P.ink,
            color: P.highlight,
            fontFamily: FONT.semi,
            fontSize: 40,
            opacity: segIn,
            transform: `translateY(${(1 - segIn) * 20}px)`,
            boxShadow: '0 12px 30px rgba(60,20,0,0.30)',
          }}
        >
          <span style={{fontSize: 42}}>{meta.icon}</span>
          <span>{meta.label}</span>
        </div>
      </AbsoluteFill>

      {/* kinetic typography mot à mot */}
      <AbsoluteFill style={{top: 640, alignItems: 'center'}}>
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            justifyContent: 'center',
            alignItems: 'baseline',
            columnGap: 16,
            rowGap: 6,
            maxWidth: 940,
            paddingLeft: 16,
            paddingRight: 16,
            fontFamily: FONT.xbold,
            fontSize: wordFont,
            lineHeight: 1.14,
            textAlign: 'center',
            color: P.ink,
            textShadow: full ? '0 3px 10px rgba(0,0,0,0.45)' : '0 2px 0 rgba(255,255,255,0.25)',
            opacity: segIn,
            transform: `translateY(${(1 - segIn) * 24}px)`,
          }}
        >
          {(() => {
            let currentWord = -1;
            seg.words.forEach((x, j) => {
              if (x.start * fps <= frame) currentWord = j;
            });
            const from = Math.max(0, currentWord - 2);
            return seg.words.map((w, i) => {
              if (i < from) return null;
              const wordFrame = w.start * fps;
              const delta = frame - wordFrame;
              const visible = delta >= 0;
              const scale = spring({
                frame: delta,
                fps,
                config: {damping: 12, stiffness: 240, mass: 0.5},
              });
              const op = interpolate(delta, [0, 3], [0, 1], {
                extrapolateLeft: 'clamp',
                extrapolateRight: 'clamp',
              });
              const isKey = KEY.has(norm(w.text));
              const isCurrent = i === currentWord;
              const pulse = isCurrent ? 1 + 0.04 * Math.sin(frame * 0.35) : 1;
              const size = isCurrent ? wordFont : Math.round(wordFont * 0.74);
              const pill = isCurrent || isKey;
              return (
                <span
                  key={i}
                  style={{
                    display: 'inline-block',
                    fontSize: size,
                    transform: `scale(${visible ? scale * pulse : 0.3})`,
                    opacity: visible ? (isCurrent ? op : op * 0.62) : 0,
                    color: isCurrent ? P.highlight : isKey ? P.pillInk : wordColor,
                    backgroundColor: pill ? (isCurrent ? P.ink : P.pillBg) : 'transparent',
                    borderRadius: pill ? 16 : 0,
                    padding: pill ? '4px 14px' : '0px',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {w.text}
                </span>
              );
            });
          })()}
        </div>
      </AbsoluteFill>

      {/* zone d'accent : preuve / paiement / CTA */}
      <AbsoluteFill style={{top: 1250, alignItems: 'center'}}>
        {isPreuve ? (
          <div
            style={{
              width: 820,
              backgroundColor: '#fffdf7',
              borderRadius: 32,
              padding: '28px 40px',
              boxShadow: '0 20px 50px rgba(60,20,0,0.35)',
              opacity: segIn,
              transform: `translateY(${(1 - segIn) * 30}px)`,
              fontFamily: FONT.semi,
              color: '#2b1206',
            }}
          >
            <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
              <span style={{fontFamily: FONT.xbold, fontSize: 44}}>{V.preuve.card_title}</span>
              <span style={{fontFamily: FONT.xbold, fontSize: 38, color: '#b45309'}}>📄</span>
            </div>
            <div style={{height: 2, backgroundColor: '#f3d9b0', margin: '14px 0'}} />
            {CARD_ROWS.map(([left, right], r) => (
              <div key={r} style={{display: 'flex', justifyContent: 'space-between', fontSize: 34, lineHeight: 1.6}}>
                <span>{left}</span>
                <span style={r === CARD_ROWS.length - 1 ? {color: '#15803d', fontFamily: FONT.xbold} : undefined}>{right}</span>
              </div>
            ))}
          </div>
        ) : null}

        {isSoulagement ? (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 20,
              padding: '26px 54px',
              borderRadius: 30,
              backgroundColor: '#15803d',
              color: '#fff',
              fontFamily: FONT.xbold,
              fontSize: 56,
              boxShadow: '0 18px 44px rgba(0,0,0,0.3)',
              opacity: segIn,
              transform: `scale(${0.8 + 0.2 * segIn})`,
            }}
          >
            <span style={{fontSize: 66}}>✓</span>
            <span>{V.soulagement.banner}</span>
          </div>
        ) : null}

        {isCta ? (
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 18,
              opacity: ctaIn,
              transform: `scale(${0.85 + 0.15 * ctaIn})`,
            }}
          >
            <div
              style={{
                fontFamily: FONT.xbold,
                fontSize: 150,
                color: P.ink,
                backgroundColor: P.highlight,
                padding: '8px 60px',
                borderRadius: 30,
                boxShadow: '0 18px 44px rgba(60,20,0,0.35)',
                transform: `scale(${0.9 + 0.1 * ctaIn}) rotate(${(1 - ctaIn) * -6}deg)`,
              }}
            >
              {JOB.prix}
            </div>
            <div style={{fontFamily: FONT.semi, fontSize: 46, color: wordColor}}>
              Lien en description
            </div>
            <div style={{fontSize: 56, lineHeight: 1}}>👇</div>
          </div>
        ) : null}
      </AbsoluteFill>

      {/* scrim de contraste (bas) */}
      <div style={{position: 'absolute', inset: 0, background: 'linear-gradient(180deg, rgba(43,18,6,0) 52%, rgba(43,18,6,0.20) 100%)'}} />

      {/* indicateur son (visualizer) */}
      <div style={{position: 'absolute', bottom: 44, right: 56, display: 'flex', alignItems: 'flex-end', gap: 6, height: 40}}>
        {[0, 1, 2, 3].map((i) => {
          const h = 12 + 24 * Math.abs(Math.sin(frame / 8 + i * 1.1));
          return <div key={i} style={{width: 8, height: h, borderRadius: 4, backgroundColor: 'rgba(43,18,6,0.55)'}} />;
        })}
      </div>

      {/* barre de progression */}
      <div style={{position: 'absolute', bottom: 40, left: 130, width: 820, height: 12, borderRadius: 999, backgroundColor: 'rgba(43,18,6,0.18)'}}>
        <div style={{width: `${progress * 100}%`, height: 12, borderRadius: 999, backgroundColor: P.ink}} />
      </div>

      {/* teinte par segment */}
      <div style={{position: 'absolute', inset: 0, backgroundColor: tint, opacity: segIn}} />

      {/* vignette chaude */}
      <div style={{position: 'absolute', inset: 0, background: 'radial-gradient(ellipse at center, rgba(0,0,0,0) 55%, rgba(60,20,0,0.28) 100%)'}} />
    </AbsoluteFill>
  );
};



