import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';

const stories = {
  recall: {
    label: 'A DECISION, REMEMBERED', color: '#d9f99d',
    beats: [
      {who: 'YOU / TODAY', title: '“Keep embeddings local.”', detail: 'Privacy matters more than the small accuracy gain.', kind: 'speech'},
      {who: 'LOREHOLM / QUIETLY', title: 'Conversation captured', detail: 'Complete source context reaches your private instance.', kind: 'capture'},
      {who: 'MINER / LATER', title: 'Decision + reason + evidence', detail: 'A claim is linked back to the exact source span.', kind: 'mine'},
      {who: 'YOU / THREE WEEKS', title: '“Why did we keep it local?”', detail: 'The answer arrives with inspectable evidence.', kind: 'answer'},
    ],
  },
  privacy: {
    label: 'A PRIVATE REQUEST', color: '#66d6cf',
    beats: [
      {who: 'YOU / BROWSER', title: 'Ask from anywhere', detail: 'The public edge confirms who you are.', kind: 'speech'},
      {who: 'FRONT DOOR', title: 'Relay, do not retain', detail: 'Only permitted application traffic enters the Tailnet.', kind: 'capture'},
      {who: 'YOUR INSTANCE', title: 'Process beside the data', detail: 'ArcadeDB and Bifrost stay on the private bridge.', kind: 'mine'},
      {who: 'BOUNDARY', title: 'Raw context stays home', detail: 'The public edge never becomes the knowledge authority.', kind: 'answer'},
    ],
  },
  offline: {
    label: 'A BROKEN CONNECTION', color: '#f4b860',
    beats: [
      {who: 'OBSERVER', title: 'Work continues', detail: 'A useful moment occurs while the tunnel is down.', kind: 'speech'},
      {who: 'EMBEDDED SPINE', title: 'Queue the envelope', detail: 'Its stable capture ID and source time are preserved.', kind: 'capture'},
      {who: 'RECONNECTED', title: 'Retry the same capture', detail: 'Delivery resumes without inventing a new event.', kind: 'mine'},
      {who: 'INSTANCE', title: 'One durable receipt', detail: 'Idempotency turns replay into exactly one memory.', kind: 'answer'},
    ],
  },
  evidence: {
    label: 'AN ANSWER YOU CAN CHALLENGE', color: '#e87951',
    beats: [
      {who: 'RAW SOURCE', title: '“Maya approved the change.”', detail: 'A sentence exists inside a larger conversation.', kind: 'speech'},
      {who: 'MINER', title: 'Resolve Maya Chen', detail: 'A mention is joined conservatively, with lineage.', kind: 'capture'},
      {who: 'KNOWLEDGE', title: 'Claim ↔ Evidence', detail: 'Time, source span, and mining version remain attached.', kind: 'mine'},
      {who: 'FUTURE ANSWER', title: 'Show your work', detail: 'Open the evidence instead of trusting fluent prose.', kind: 'answer'},
    ],
  },
  builder: {
    label: 'A NEW OBSERVER', color: '#b8b5ff',
    beats: [
      {who: 'YOUR ADAPTER', title: 'Notice source context', detail: 'Observe facts the host application already knows.', kind: 'speech'},
      {who: 'EMBEDDED SPINE', title: 'Normalize + apply policy', detail: 'Create one canonical, retryable capture envelope.', kind: 'capture'},
      {who: 'INSTANCE', title: 'Authenticate + stage', detail: 'Return a receipt only after durable local storage.', kind: 'mine'},
      {who: 'MINER / NOT CLIENT', title: 'Decide what becomes lore', detail: 'Integrations never write graph truth directly.', kind: 'answer'},
    ],
  },
};

const icons = {
  speech: '“ ”', capture: '◎', mine: '◇', answer: '↗',
};

export const SystemMap = ({scenario = 'recall'}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const story = stories[scenario] || stories.recall;
  const progress = interpolate(frame, [0, 340], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

  return (
    <AbsoluteFill style={{background: '#080b09', color: '#eee7d3', overflow: 'hidden', fontFamily: 'IBM Plex Mono, monospace'}}>
      <div style={{position: 'absolute', inset: 0, opacity: .12, backgroundImage: `linear-gradient(${story.color}25 1px,transparent 1px),linear-gradient(90deg,${story.color}25 1px,transparent 1px)`, backgroundSize: '36px 36px'}} />
      <div style={{position: 'absolute', width: 520, height: 520, borderRadius: '50%', left: `${-20 + progress * 90}%`, top: -210, background: `radial-gradient(circle, ${story.color}20, transparent 67%)`}} />

      <header style={{position: 'absolute', left: 44, right: 44, top: 31, display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
        <span style={{color: story.color, fontSize: 11, letterSpacing: 3}}>{story.label}</span>
        <span style={{color: '#59615b', fontSize: 9, letterSpacing: 2}}>USE CASE / {String(Math.min(4, Math.floor(frame / 85) + 1)).padStart(2, '0')}</span>
      </header>

      <div style={{position: 'absolute', left: 62, right: 62, top: 118, height: 2, background: '#252b27'}}>
        <div style={{height: '100%', width: `${progress * 100}%`, background: story.color, boxShadow: `0 0 18px ${story.color}`}} />
      </div>

      <div style={{position: 'absolute', inset: '91px 38px 48px', display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 13, alignItems: 'center'}}>
        {story.beats.map((beat, index) => {
          const start = index * 78;
          const enter = spring({frame: frame - start, fps, config: {damping: 18, stiffness: 95}});
          const focus = interpolate(frame, [start - 15, start + 8, start + 72, start + 96], [.38, 1, 1, .46], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
          return (
            <div key={beat.title} style={{position: 'relative', minHeight: 225, padding: '27px 21px 21px', border: `1px solid ${index === 3 ? story.color + '80' : '#303731'}`, background: index === 3 ? `${story.color}0b` : '#0d110f', opacity: enter * focus, transform: `translateY(${interpolate(enter, [0, 1], [24, 0])}px)`}}>
              <div style={{position: 'absolute', width: 15, height: 15, left: '50%', top: -28, transform: 'translateX(-50%) rotate(45deg)', border: `1px solid ${story.color}`, background: frame >= start ? story.color : '#0d110f', boxShadow: frame >= start ? `0 0 15px ${story.color}` : 'none'}} />
              <div style={{color: story.color, fontSize: 9, letterSpacing: 1.8}}>{String(index + 1).padStart(2, '0')} / {beat.who}</div>
              <div style={{color: story.color, fontFamily: 'Newsreader, serif', fontSize: 30, lineHeight: 1, margin: '27px 0 20px'}}>{icons[beat.kind]}</div>
              <div style={{fontFamily: 'Unbounded, sans-serif', fontSize: 14, lineHeight: 1.3, letterSpacing: '-.4px'}}>{beat.title}</div>
              <div style={{fontFamily: 'Newsreader, serif', color: '#92988f', fontSize: 17, lineHeight: 1.28, marginTop: 12}}>{beat.detail}</div>
            </div>
          );
        })}
      </div>

      <div style={{position: 'absolute', left: 44, bottom: 21, color: '#59615b', fontSize: 8, letterSpacing: 1.8}}>NOT A TOPOLOGY DIAGRAM — A MOMENT IN USE</div>
    </AbsoluteFill>
  );
};
