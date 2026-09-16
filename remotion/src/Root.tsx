import React from 'react';
import {Composition} from 'remotion';
import {CashShort} from './CashShort';
import {DURATION_S} from './data/captions';

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="CashShort"
      component={CashShort}
      durationInFrames={Math.ceil(DURATION_S * 30)}
      fps={30}
      width={1080}
      height={1920}
    />
  );
};
