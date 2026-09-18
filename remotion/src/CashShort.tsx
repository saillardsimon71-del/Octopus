import React, {useEffect} from 'react';
import {
  AbsoluteFill,
  Img,
  OffthreadVideo,
  Sequence,
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
type Meta = {label: string; icon: string; img: string; img2?: string; clip?: string; clip2?: string;
  accent: Accent; full: boolean};

// Libelles propres a l'offre : fournis par le job (champ `visuel`), sinon ceux de l'offre Impayes
// d'origine. Avant, ils etaient en dur : la video Devis/CGV affichait "FACTURE IMPAYEE" (audit C6).
type RoleText = {label?: string; icon?: string; accent_emoji?: string; accent_text?: string;
  img?: string; img2?: string; clip?: string; clip2?: string};
type Visuel = {
  hook?: RoleText;
  douleur?: RoleText;
  preuve?: RoleText & {card_title?: string; card_rows?: [string, string][]};
  soulagement?: RoleText & {banner?: string};
  cta?: RoleText;
};
// Repli generique tire du job lui-meme. Avant, il reprenait les libelles de l'offre Impayes :
// une offre generee sans `visuel` affichait "FACTURE IMPAYEE" et "+1 240 EUR ENCAISSE", chiffres
// d'une autre offre (audit C6). Ici, rien n'est invente : pas de libelle -> pas d'element.
const TITLE = String((JOB as unknown as {titre?: string}).titre ?? '').split(/[:–-]/)[0].trim();
const DEFAULT_VISUEL: Required<Visuel> = {
  hook: {label: TITLE.toUpperCase(), icon: '📌', accent_emoji: '', accent_text: ''},
  douleur: {label: 'CE QUE ÇA COÛTE', icon: '⚠️', accent_emoji: '', accent_text: ''},
  preuve: {label: 'LA SOLUTION', icon: '✅', accent_emoji: '', accent_text: '', card_title: '', card_rows: []},
  soulagement: {label: 'LE RÉSULTAT', icon: '🤝', accent_emoji: '', accent_text: '', banner: ''},
  cta: {label: 'À VOUS DE JOUER', icon: '👇', accent_emoji: '👇', accent_text: ''},
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
// Carte image des segments non plein ecran : elle descend derriere les sous-titres, qui ont
// leurs propres fonds. Sinon le bas de l'ecran reste vide sur la moitie de la video.
const CARD_H = 1040;

const meta = (role: keyof Visuel, img: string, full: boolean, bg: string, accentText?: string): Meta => ({
  label: V[role].label ?? '',
  icon: V[role].icon ?? '',
  img,
  img2: V[role].img2,
  clip: V[role].clip,
  clip2: V[role].clip2,
  full,
  accent: {emoji: V[role].accent_emoji ?? '', text: accentText ?? V[role].accent_text ?? '', bg},
});

// `img` vient du job quand tools/fetch_broll.py a trouve une image libre pour l'offre ;
// sinon on garde les photos livrees avec le depot.
const SEG: Record<string, Meta> = {
  hook: meta('hook', V.hook.img ?? 'human.jpg', true, '#d90429'),
  douleur: meta('douleur', V.douleur.img ?? 'human2.jpg', true, '#b91c1c'),
  preuve: meta('preuve', V.preuve.img ?? 'human.jpg', true, '#b45309'),
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
  hook: 'rgba(217,4,41,0.10)',
  douleur: 'rgba(185,28,28,0.11)',
  preuve: 'rgba(234,120,20,0.12)',
  soulagement: 'rgba(21,128,61,0.11)',
  cta: 'rgba(234,88,12,0.12)',
};
// Grain argentique : un SVG de bruit fige, applique en superposition. Sans lui, l'aplat numerique
// des degrades se voit immediatement sur un telephone.
// Etalonnage commun : les photos libres sont souvent ternes, la marque est chaude et vive.
const GRADE = 'saturate(1.35) contrast(1.06) brightness(1.02)';
const GRAIN = `url("data:image/svg+xml;utf8,${encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="220" height="220">' +
  '<filter id="n"><feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="3" stitchTiles="stitch"/>' +
  '<feColorMatrix type="saturate" values="0"/></filter>' +
  '<rect width="220" height="220" filter="url(#n)" opacity="0.5"/></svg>'
)}")`;


/** Anime les nombres d'un texte ("+1 240 € ENCAISSÉ") : ils defilent jusqu'a leur valeur. */
const countUp = (text: string, progress: number): string =>
  text.replace(/\d[\d  .]*\d|\d/g, (raw) => {
    const value = parseInt(raw.replace(/[^0-9]/g, ''), 10);
    if (!Number.isFinite(value)) return raw;
    return Math.round(value * progress).toLocaleString('fr-FR').replace(/ /g, ' ');
  });

type BackdropProps = {meta: Meta; prog: number; appear: number; opacity: number; frame: number;
  elapsed: number; segStartFrame: number; fps: number};

/** Image d'un segment : zoom lent, fondu d'entree, et fondu croise avec le segment precedent. */
const BEAT_S = 1.8;

const Backdrop: React.FC<BackdropProps> = ({meta, prog, appear, opacity, frame, elapsed, segStartFrame, fps}) => {
  if (opacity <= 0.01) return null;
  // Un plan fixe de 3 s ne tient pas : on change de cadrage toutes les 1,8 s sur la meme photo.
  const beat = Math.floor(elapsed / BEAT_S);
  const inBeat = (elapsed % BEAT_S) / BEAT_S;
  const tight = beat % 2 === 1;
  const clip = tight && meta.clip2 ? meta.clip2 : meta.clip;     // rush filme si le segment en a un
  const shot = tight && meta.img2 ? meta.img2 : meta.img;        // sinon photo
  // Un rush bouge deja : on se contente d'une legere poussee. Une photo doit tout au zoom.
  const zoom = clip ? 1.0 + inBeat * 0.03 : (tight && !meta.img2 ? 1.26 : 1.0) + inBeat * 0.1;
  const drift = (tight ? 2.5 : -2.5) + inBeat * (tight ? -2 : 2);
  const originY = tight ? '38%' : '52%';
  const accent = meta.accent;
  const badge = (size: number, top: number, right: number) => !accent.text ? null : (
    <div style={{position: 'absolute', top, right, display: 'flex', alignItems: 'center', gap: 12,
      padding: '14px 26px', borderRadius: 24, backgroundColor: accent.bg, color: '#fff',
      fontFamily: FONT.xbold, fontSize: size, boxShadow: '0 10px 26px rgba(0,0,0,0.35)',
      transform: `rotate(${Math.sin(frame / 12) * 2}deg) scale(${0.9 + 0.1 * appear})`, opacity: appear}}>
      <span style={{fontSize: size + 2}}>{accent.emoji}</span>
      <span>{accent.text}</span>
    </div>
  );
  const mediaStyle: React.CSSProperties = {
    width: '100%', height: '100%', objectFit: 'cover', filter: GRADE,
    transformOrigin: `50% ${originY}`,
    transform: `scale(${zoom * (1 + 0.035 * (1 - appear))}) translateX(${clip ? drift * 0.3 : drift}%)`,
  };
  const media = clip ? (
    <Sequence from={Math.max(0, Math.round(segStartFrame + beat * BEAT_S * fps))}
      durationInFrames={Math.ceil(BEAT_S * fps) + 2} layout="none">
      <OffthreadVideo src={staticFile(`video/${clip}`)} muted style={mediaStyle} />
    </Sequence>
  ) : (
    <Img src={staticFile(`img/${shot}`)} style={mediaStyle} />
  );

  if (meta.full) {
    return (
      <AbsoluteFill style={{opacity}}>
        {media}
        <div style={{position: 'absolute', inset: 0, background: 'linear-gradient(180deg, rgba(43,18,6,0.30) 0%, rgba(43,18,6,0.05) 40%, rgba(43,18,6,0.45) 100%)'}} />
        {badge(44, 90, 50)}
      </AbsoluteFill>
    );
  }
  return (
    <AbsoluteFill style={{top: 100, left: 70, width: 940, height: CARD_H, opacity: opacity * appear,
      transform: `scale(${0.96 + 0.04 * appear})`, transformOrigin: '50% 50%'}}>
      <div style={{position: 'absolute', inset: 0, borderRadius: 44, overflow: 'hidden', boxShadow: '0 24px 60px rgba(60,20,0,0.35)'}}>
        {media}
        <div style={{position: 'absolute', inset: 0, background: 'linear-gradient(180deg, rgba(43,18,6,0.02) 0%, rgba(43,18,6,0.48) 100%)'}} />
      </div>
      {badge(40, 26, 26)}
    </AbsoluteFill>
  );
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

  // Segment courant = le dernier commence. Pendant les silences entre deux segments, on reste
  // sur le precedent : sinon l'etiquette et les sous-titres disparaissent et l'ecran meurt.
  let segIdx = 0;
  for (let i = 0; i < SEGS.length; i++) {
    if (t >= SEGS[i].start) segIdx = i;
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
  // Coupe franche entre segments (le rythme d'un Short vient des coupes), adoucie sur une image
  // seulement : assez pour eviter le clignotement, assez sec pour rester percu comme une coupe.
  const prevSeg = SEGS[Math.max(0, segIdx - 1)];
  const prevMeta = SEG[prevSeg.role] ?? SEG.hook;
  const cross = segIdx === 0 ? 1 : interpolate(t, [seg.start, seg.start + 0.04], [0, 1], {
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
  // Le premier segment porte l'accroche : il se lit plus gros que le reste.
  const hookBoost = segIdx === 0 ? 1.18 : 1;
  const wordFont = Math.round(Math.max(74, Math.min(108, Math.round(1020 / (maxChars * 0.58)))) * hookBoost);

  const isCta = seg.role === 'cta';
  const isPreuve = seg.role === 'preuve';
  const isSoulagement = seg.role === 'soulagement';
  const ctaIn = interpolate(t, [seg.start, seg.start + 0.3], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const progress = frame / durationInFrames;
  // Apparition ressort de la zone d'accent + defilement des montants.
  const accentSpring = spring({frame: Math.max(0, frame - seg.start * fps), fps,
    config: {damping: 14, stiffness: 180, mass: 0.7}});
  const countProg = interpolate(t, [seg.start + 0.15, seg.start + 1.1], [0, 1],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

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

      <Backdrop meta={prevMeta} prog={1} appear={1} opacity={1 - cross} frame={frame}
        elapsed={Math.max(0, t - prevSeg.start)} segStartFrame={prevSeg.start * fps} fps={fps} />
      <Backdrop meta={meta} prog={segProg} appear={segIn} opacity={cross} frame={frame}
        elapsed={t - seg.start} segStartFrame={seg.start * fps} fps={fps} />
      {/* boucle : la derniere image revient vers la premiere, le replay se declenche sans decision */}
      <Backdrop meta={SEG.hook} prog={0} appear={0} frame={frame} elapsed={0}
        segStartFrame={durationInFrames - 0.55 * fps} fps={fps}
        opacity={interpolate(t, [durationInFrames / fps - 0.55, durationInFrames / fps - 0.05], [0, 1],
          {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})} />

      <AbsoluteFill style={{top: 170, alignItems: 'center'}}>
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
      <AbsoluteFill style={{top: 1220, alignItems: 'center'}}>
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
            // 2 a 4 mots par image : au-dela, l'oeil lit au lieu d'ecouter.
            const from = Math.max(0, currentWord - 1);
            const to = Math.min(seg.words.length - 1, Math.max(currentWord, 0) + 1);
            return seg.words.map((w, i) => {
              if (i < from || i > to) return null;
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
                    color: isCurrent ? P.highlight : isKey ? P.pillInk : '#fff',
                    backgroundColor: isCurrent ? P.ink : isKey ? P.pillBg : 'rgba(18,7,2,0.42)',
                    borderRadius: 16,
                    padding: '4px 14px',
                    textShadow: isCurrent || isKey ? 'none' : '0 4px 16px rgba(0,0,0,0.55)',
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
      <AbsoluteFill style={{top: 700, alignItems: 'center'}}>
        {isPreuve && CARD_ROWS.length > 0 ? (
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
            {CARD_ROWS.map(([left, right], r) => {
              const rowIn = spring({frame: Math.max(0, frame - (seg.start + 0.25 + r * 0.18) * fps), fps,
                config: {damping: 16, stiffness: 200, mass: 0.6}});
              return (
                <div key={r} style={{display: 'flex', justifyContent: 'space-between', fontSize: 34,
                  lineHeight: 1.6, opacity: rowIn, transform: `translateX(${(1 - rowIn) * 26}px)`}}>
                  <span>{left}</span>
                  <span style={r === CARD_ROWS.length - 1 ? {color: '#15803d', fontFamily: FONT.xbold} : undefined}>
                    {countUp(right, countProg)}
                  </span>
                </div>
              );
            })}
          </div>
        ) : null}

        {isSoulagement && V.soulagement.banner ? (
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
              transform: `scale(${0.78 + 0.22 * accentSpring}) rotate(${(1 - accentSpring) * -2}deg)`,
            }}
          >
            <span style={{fontSize: 66}}>✓</span>
            <span>{countUp(String(V.soulagement.banner ?? ''), countProg)}</span>
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
            <div style={{fontFamily: FONT.semi, fontSize: 46, color: '#fff',
              backgroundColor: 'rgba(18,7,2,0.5)', padding: '8px 26px', borderRadius: 999,
              textShadow: '0 4px 16px rgba(0,0,0,0.5)'}}>
              Lien en description
            </div>
            {/* la fleche rebondit : un appel a l'action fixe ne se voit pas */}
            <div style={{fontSize: 56, lineHeight: 1,
              transform: `translateY(${Math.abs(Math.sin(frame / 7)) * -18}px)`}}>👇</div>
          </div>
        ) : null}
      </AbsoluteFill>

      {/* teinte par segment */}
      <div style={{position: 'absolute', inset: 0, backgroundColor: tint, opacity: segIn}} />

      {/* lisibilite du tiers inferieur : les sous-titres passent sur n'importe quelle photo */}
      <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: 900,
        background: 'linear-gradient(180deg, rgba(20,8,2,0) 0%, rgba(20,8,2,0.30) 45%, rgba(20,8,2,0.62) 100%)'}} />

      {/* vignette + grain : ce qui separe une image plate d'une image filmee */}
      <div style={{position: 'absolute', inset: 0, background: 'radial-gradient(ellipse at 50% 42%, rgba(0,0,0,0) 48%, rgba(30,10,0,0.42) 100%)'}} />
      <div style={{position: 'absolute', inset: -40, backgroundImage: GRAIN, backgroundRepeat: 'repeat',
        opacity: 0.09, mixBlendMode: 'overlay',
        transform: `translate(${(frame % 5) * 7 - 14}px, ${(frame % 3) * 9 - 9}px)`}} />

      {/* eclat court a chaque coupe : deux images suffisent a marquer le changement de plan */}
      <div style={{position: 'absolute', inset: 0, backgroundColor: accent.bg, pointerEvents: 'none',
        opacity: segIdx === 0 ? 0 : interpolate(t, [seg.start, seg.start + 0.03, seg.start + 0.16], [0, 0.32, 0],
          {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})}} />

      {/* reperes d'interface : au-dessus du grain, en clair, sinon ils disparaissent dans le degrade */}
      <div style={{position: 'absolute', bottom: 44, right: 56, display: 'flex', alignItems: 'flex-end', gap: 6, height: 40}}>
        {[0, 1, 2, 3].map((i) => {
          const h = 12 + 24 * Math.abs(Math.sin(frame / 8 + i * 1.1));
          return <div key={i} style={{width: 8, height: h, borderRadius: 4, backgroundColor: 'rgba(255,255,255,0.75)'}} />;
        })}
      </div>

      <div style={{position: 'absolute', bottom: 40, left: 130, width: 820, height: 10, borderRadius: 999, backgroundColor: 'rgba(255,255,255,0.22)'}}>
        <div style={{width: `${progress * 100}%`, height: 10, borderRadius: 999, backgroundColor: P.highlight,
          boxShadow: '0 0 18px rgba(255,255,255,0.35)'}} />
      </div>
    </AbsoluteFill>
  );
};



