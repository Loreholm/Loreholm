import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';

const stories = {
  recall: {
    label: 'A DECISION, REMEMBERED', color: '#d9f99d',
    beats: [
      {who: 'YOU / TODAY', title: '“Keep this private.”', detail: 'You explain why privacy matters more than a small accuracy gain.', kind: 'speech'},
      {who: 'LOREHOLM / QUIETLY', title: 'The moment is remembered', detail: 'The conversation stays with the rest of your private work.', kind: 'capture'},
      {who: 'LOREHOLM / LATER', title: 'Reason and source stay together', detail: 'The decision is connected to when and why it was made.', kind: 'mine'},
      {who: 'YOU / THREE WEEKS', title: '“Why did we choose that?”', detail: 'The answer can open the original conversation behind it.', kind: 'answer'},
    ],
  },
  privacy: {
    label: 'A PRIVATE REQUEST', color: '#66d6cf',
    beats: [
      {who: 'YOU / AWAY FROM HOME', title: 'Sign in from anywhere', detail: 'Loreholm confirms that the request really came from you.', kind: 'speech'},
      {who: 'THE GUARDED HARBOR', title: 'A private road opens', detail: 'Only the allowed request is carried toward your instance.', kind: 'capture'},
      {who: 'YOUR INSTANCE', title: 'Your memory answers', detail: 'The saved context and local intelligence stay on your ground.', kind: 'mine'},
      {who: 'THE BOUNDARY', title: 'Lasting context stays home', detail: 'The public service helps you reach it without becoming its owner.', kind: 'answer'},
    ],
  },
  offline: {
    label: 'A BROKEN CONNECTION', color: '#f4b860',
    beats: [
      {who: 'YOU', title: 'Work continues', detail: 'A useful moment happens while the private connection is down.', kind: 'speech'},
      {who: 'LOREHOLM', title: 'The moment waits safely', detail: 'The connected tool keeps it nearby instead of throwing it away.', kind: 'capture'},
      {who: 'RECONNECTED', title: 'The same moment travels once', detail: 'Delivery resumes when the private road becomes available.', kind: 'mine'},
      {who: 'YOUR INSTANCE', title: 'One memory, not two', detail: 'A repeated delivery does not create a duplicate story.', kind: 'answer'},
    ],
  },
  evidence: {
    label: 'AN ANSWER YOU CAN CHALLENGE', color: '#e87951',
    beats: [
      {who: 'A CONVERSATION', title: '“Maya approved the change.”', detail: 'One sentence appears inside a much larger discussion.', kind: 'speech'},
      {who: 'LOREHOLM', title: 'Meaning is connected carefully', detail: 'It asks which Maya, what change, and when this was true.', kind: 'capture'},
      {who: 'THE REMEMBERED FACT', title: 'The source stays attached', detail: 'The understanding keeps a path back to the original words.', kind: 'mine'},
      {who: 'A FUTURE ANSWER', title: 'You can inspect the proof', detail: 'Open the source instead of trusting polished prose alone.', kind: 'answer'},
    ],
  },
  builder: {
    label: 'A NEW OBSERVER', color: '#b8b5ff',
    beats: [
      {who: 'THE CONNECTED TOOL', title: 'Notice useful context', detail: 'Observe what the application already knows about your work.', kind: 'speech'},
      {who: 'THE SHARED CLIENT LAYER', title: 'Package it consistently', detail: 'Apply your rules and prepare the moment for safe delivery.', kind: 'capture'},
      {who: 'YOUR INSTANCE', title: 'Confirm and store it', detail: 'A receipt returns only after the context is safely kept.', kind: 'mine'},
      {who: 'THE KNOWLEDGE BUILDER', title: 'Decide what becomes lore', detail: 'Individual tools never get to declare what is true.', kind: 'answer'},
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
              <div style={{fontFamily: 'Almendra, Georgia, serif', fontWeight: 700, fontSize: 18, lineHeight: 1.15, letterSpacing: '-.2px'}}>{beat.title}</div>
              <div style={{fontFamily: 'Newsreader, serif', color: '#92988f', fontSize: 17, lineHeight: 1.28, marginTop: 12}}>{beat.detail}</div>
            </div>
          );
        })}
      </div>

      <div style={{position: 'absolute', left: 44, bottom: 21, color: '#59615b', fontSize: 8, letterSpacing: 1.8}}>NOT A TOPOLOGY DIAGRAM — A MOMENT IN USE</div>
    </AbsoluteFill>
  );
};
