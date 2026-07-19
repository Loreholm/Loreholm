import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {getVisualization} from './visualizations';

const typeLabels = {
  flow: 'PROCESS MAP', compare: 'TRADE-OFF MAP', boundary: 'TRUST BOUNDARY', timeline: 'TIME MAP',
  network: 'RELATION MAP', stack: 'LAYER MAP', funnel: 'SELECTION MAP',
};

const enterAt = (frame, fps, index, spacing = 24) => spring({
  frame: frame - 18 - index * spacing,
  fps,
  config: {damping: 18, stiffness: 105, mass: .8, overshootClamping: true},
});

const fadeStyle = (amount, distance = 18) => ({
  opacity: amount,
  transform: `translateY(${interpolate(amount, [0, 1], [distance, 0])}px)`,
});

const labelOf = (value) => typeof value === 'string' ? value : value.label;
const detailOf = (value) => typeof value === 'string' ? '' : value.detail;

const FlowDiagram = ({scene, frame, fps, color}) => {
  const travel = interpolate(frame, [38, 300], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <div style={{position: 'absolute', inset: '94px 43px 58px'}}>
      <div style={{position: 'absolute', left: 74, right: 74, top: 116, height: 2, background: '#27302b'}}>
        <div style={{height: 2, width: `${travel * 100}%`, background: color, boxShadow: `0 0 15px ${color}`}} />
        <div style={{position: 'absolute', left: `${travel * 100}%`, top: -5, width: 12, height: 12, borderRadius: '50%', transform: 'translateX(-50%)', background: color, boxShadow: `0 0 20px ${color}`}} />
      </div>
      <div style={{height: '100%', display: 'grid', gridTemplateColumns: `repeat(${scene.items.length}, 1fr)`, gap: 18}}>
        {scene.items.map((stage, index) => {
          const entered = enterAt(frame, fps, index);
          const active = frame >= 45 + index * 65;
          return (
            <div key={stage.label} style={{...fadeStyle(entered), display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center'}}>
              <div style={{height: 91, display: 'flex', alignItems: 'flex-end'}}>
                <div style={{width: 70, height: 70, display: 'grid', placeItems: 'center', transform: 'rotate(45deg)', border: `1px solid ${active ? color : '#374039'}`, background: active ? `${color}17` : '#0d120f', boxShadow: active ? `inset 0 0 25px ${color}12` : 'none'}}>
                  <span style={{transform: 'rotate(-45deg)', color: active ? color : '#667169', fontSize: 11, letterSpacing: 2}}>{String(index + 1).padStart(2, '0')}</span>
                </div>
              </div>
              <div style={{height: 50}} />
              <b style={{maxWidth: 185, color: '#eee7d3', font: '700 19px/1.05 Almendra, Georgia, serif'}}>{stage.label}</b>
              <p style={{maxWidth: 185, margin: '10px 0 0', color: '#889188', font: '15px/1.25 Newsreader, Georgia, serif'}}>{stage.detail}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
};

const CompareDiagram = ({scene, frame, fps, color}) => (
  <div style={{position: 'absolute', inset: '88px 40px 57px', display: 'grid', gridTemplateColumns: '1fr 72px 1fr', alignItems: 'stretch'}}>
    {[scene.left, scene.right].map((column, columnIndex) => {
      const entered = enterAt(frame, fps, columnIndex, 35);
      return (
        <React.Fragment key={column.label}>
          {columnIndex === 1 && (
            <div style={{position: 'relative', display: 'grid', placeItems: 'center'}}>
              <div style={{position: 'absolute', top: 0, bottom: 0, width: 1, background: '#303832', transform: `scaleY(${interpolate(frame, [20, 110], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})})`}} />
              <span style={{zIndex: 1, width: 39, height: 39, display: 'grid', placeItems: 'center', transform: 'rotate(45deg)', border: `1px solid ${color}`, background: '#0a0f0c', color}}><i style={{transform: 'rotate(-45deg)', font: 'normal 9px IBM Plex Mono, monospace'}}>OR</i></span>
            </div>
          )}
          <section style={{...fadeStyle(entered, columnIndex ? 14 : -14), position: 'relative', padding: '29px 34px', borderTop: `2px solid ${columnIndex ? '#59645d' : color}`, background: columnIndex ? 'linear-gradient(160deg,#111512,#0b0f0c)' : `linear-gradient(160deg,${color}12,#0b0f0c 70%)`}}>
            <span style={{color: columnIndex ? '#7a847c' : color, fontSize: 9, letterSpacing: 2.4}}>{columnIndex ? 'PATH / B' : 'PATH / A'}</span>
            <h3 style={{margin: '15px 0 23px', color: '#eee7d3', font: '700 28px/1 Almendra, Georgia, serif'}}>{column.label}</h3>
            <div style={{display: 'grid', gap: 12}}>
              {column.items.map((line, index) => {
                const itemEntered = enterAt(frame, fps, index + columnIndex, 18);
                return <div key={line} style={{...fadeStyle(itemEntered, 8), display: 'grid', gridTemplateColumns: '17px 1fr', gap: 10, color: '#aab0a7', font: '16px/1.25 Newsreader, Georgia, serif'}}><i style={{width: 7, height: 7, marginTop: 7, transform: 'rotate(45deg)', border: `1px solid ${columnIndex ? '#667169' : color}`, background: columnIndex ? 'transparent' : `${color}22`}} />{line}</div>;
              })}
            </div>
          </section>
        </React.Fragment>
      );
    })}
  </div>
);

const BoundaryDiagram = ({scene, frame, fps, color}) => {
  const signal = interpolate(frame, [42, 300], [8, 92], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <div style={{position: 'absolute', inset: '92px 40px 62px'}}>
      <div style={{position: 'absolute', left: `${signal}%`, top: 24, bottom: 24, width: 1, background: `linear-gradient(transparent,${color},transparent)`, boxShadow: `0 0 16px ${color}`, opacity: .8}} />
      <div style={{height: '100%', display: 'grid', gridTemplateColumns: `repeat(${scene.zones.length}, 1fr)`, gap: 8}}>
        {scene.zones.map((zone, index) => {
          const entered = enterAt(frame, fps, index, 34);
          return (
            <section key={zone.label} style={{...fadeStyle(entered), position: 'relative', display: 'flex', flexDirection: 'column', justifyContent: 'center', padding: '34px 28px', border: `1px solid ${index === 0 ? color + '80' : '#333b35'}`, background: index === 0 ? `${color}0c` : index === scene.zones.length - 1 ? '#12110e' : '#0d120f'}}>
              <span style={{position: 'absolute', left: 18, top: 15, color: index === 0 ? color : '#68736c', fontSize: 8, letterSpacing: 2}}>ZONE / {String(index + 1).padStart(2, '0')}</span>
              <div style={{color: index === 0 ? color : '#7e8980', font: '38px/1 Newsreader, Georgia, serif'}}>{index === 0 ? '⌂' : index === scene.zones.length - 1 ? '◇' : '╫'}</div>
              <h3 style={{margin: '18px 0 12px', color: '#eee7d3', font: '700 25px/1.02 Almendra, Georgia, serif'}}>{zone.label}</h3>
              <p style={{margin: 0, color: '#919990', font: '16px/1.28 Newsreader, Georgia, serif'}}>{zone.detail}</p>
              {index < scene.zones.length - 1 && <span style={{position: 'absolute', zIndex: 2, right: -17, top: '50%', width: 26, height: 26, display: 'grid', placeItems: 'center', transform: 'translateY(-50%) rotate(45deg)', border: `1px solid ${color}80`, background: '#090e0b', color}}><i style={{transform: 'rotate(-45deg)', fontStyle: 'normal'}}>›</i></span>}
            </section>
          );
        })}
      </div>
    </div>
  );
};

const TimelineDiagram = ({scene, frame, fps, color}) => {
  const progress = interpolate(frame, [32, 305], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <div style={{position: 'absolute', inset: '91px 40px 58px'}}>
      <div style={{position: 'absolute', left: 60, right: 60, top: '50%', height: 2, background: '#29312c'}}><div style={{width: `${progress * 100}%`, height: 2, background: color, boxShadow: `0 0 14px ${color}`}} /></div>
      <div style={{height: '100%', display: 'grid', gridTemplateColumns: `repeat(${scene.items.length},1fr)`}}>
        {scene.items.map((event, index) => {
          const entered = enterAt(frame, fps, index, 38);
          const above = index % 2 === 0;
          return (
            <div key={event.label} style={{...fadeStyle(entered, above ? 15 : -15), position: 'relative', display: 'grid', gridTemplateRows: '1fr 44px 1fr', textAlign: 'center'}}>
              <div style={{gridRow: above ? 1 : 3, alignSelf: above ? 'end' : 'start', padding: above ? '0 15px 19px' : '19px 15px 0'}}>
                <span style={{color, fontSize: 8, letterSpacing: 1.6}}>{String(index + 1).padStart(2, '0')}</span>
                <h3 style={{margin: '6px 0', color: '#eee7d3', font: '700 20px/1.05 Almendra, Georgia, serif'}}>{event.label}</h3>
                <p style={{margin: 0, color: '#8b948c', font: '14px/1.2 Newsreader, Georgia, serif'}}>{event.detail}</p>
              </div>
              <div style={{gridRow: 2, display: 'grid', placeItems: 'center'}}><i style={{width: 16, height: 16, transform: 'rotate(45deg)', border: `2px solid ${color}`, background: '#0a0f0c', boxShadow: `0 0 12px ${color}55`}} /></div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

const networkPositions = [[13, 17], [68, 13], [11, 68], [70, 66]];
const NetworkDiagram = ({scene, frame, fps, color}) => {
  const lineProgress = interpolate(frame, [30, 180], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <div style={{position: 'absolute', inset: '82px 44px 56px'}}>
      <svg viewBox="0 0 912 342" style={{position: 'absolute', inset: 0, width: '100%', height: '100%', overflow: 'visible'}} aria-hidden="true">
        {networkPositions.slice(0, scene.items.length).map(([x, y], index) => {
          const x1 = x / 100 * 912 + 90; const y1 = y / 100 * 342 + 30;
          return <line key={index} x1={x1} y1={y1} x2="456" y2="171" stroke={color} strokeOpacity=".55" strokeWidth="1.5" pathLength="1" strokeDasharray="1" strokeDashoffset={1 - lineProgress} />;
        })}
      </svg>
      <div style={{position: 'absolute', left: '50%', top: '50%', width: 194, height: 122, transform: 'translate(-50%,-50%)', display: 'grid', placeContent: 'center', textAlign: 'center', border: `1px solid ${color}`, borderRadius: '50%', background: `radial-gradient(circle,${color}19,#0b100d 72%)`, boxShadow: `0 0 45px ${color}12`}}>
        <small style={{color, fontSize: 8, letterSpacing: 2}}>CENTER</small>
        <b style={{marginTop: 8, color: '#eee7d3', font: '700 23px/1 Almendra, Georgia, serif'}}>{scene.center.label}</b>
        <span style={{maxWidth: 150, margin: '7px auto 0', color: '#8e978f', font: '13px/1.15 Newsreader, Georgia, serif'}}>{scene.center.detail}</span>
      </div>
      {scene.items.map((node, index) => {
        const [left, top] = networkPositions[index];
        const entered = enterAt(frame, fps, index, 29);
        return (
          <div key={node.label} style={{...fadeStyle(entered), position: 'absolute', left: `${left}%`, top: `${top}%`, width: 190, minHeight: 76, padding: '13px 15px', borderLeft: `2px solid ${color}`, background: '#0d120f', boxShadow: '0 12px 28px rgba(0,0,0,.24)'}}>
            <b style={{color: '#eee7d3', font: '700 17px/1 Almendra, Georgia, serif'}}>{node.label}</b>
            <p style={{margin: '7px 0 0', color: '#89928a', font: '13px/1.16 Newsreader, Georgia, serif'}}>{node.detail}</p>
          </div>
        );
      })}
    </div>
  );
};

const StackDiagram = ({scene, frame, fps, color}) => (
  <div style={{position: 'absolute', inset: '86px 90px 61px', display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', alignItems: 'center', gap: 7}}>
    {[...scene.items].reverse().map((layer, reverseIndex) => {
      const index = scene.items.length - reverseIndex - 1;
      const entered = enterAt(frame, fps, reverseIndex, 31);
      const width = 64 + index * 8;
      return (
        <div key={layer.label} style={{...fadeStyle(entered, 22), width: `${width}%`, minHeight: 58, display: 'grid', gridTemplateColumns: '42px 1fr', alignItems: 'center', padding: '10px 24px', border: `1px solid ${index === 0 ? color + '85' : '#354039'}`, background: index === 0 ? `${color}12` : `linear-gradient(90deg,#101612,#0c100e)`, clipPath: 'polygon(2% 0,98% 0,100% 100%,0 100%)'}}>
          <span style={{color, fontSize: 9, letterSpacing: 1.8}}>{String(index + 1).padStart(2, '0')}</span>
          <div><b style={{color: '#eee7d3', font: '700 18px/1 Almendra, Georgia, serif'}}>{layer.label}</b><span style={{marginLeft: 15, color: '#8d968e', font: '14px/1.2 Newsreader, Georgia, serif'}}>{layer.detail}</span></div>
        </div>
      );
    })}
  </div>
);

const FunnelDiagram = ({scene, frame, fps, color}) => {
  const progress = interpolate(frame, [28, 270], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const inputRows = scene.inputs.map((_, index) => 37 + index * (270 / Math.max(scene.inputs.length - 1, 1)));
  return (
    <div style={{position: 'absolute', inset: '82px 43px 57px', display: 'grid', gridTemplateColumns: '1fr 250px 1fr', alignItems: 'center'}}>
      <svg viewBox="0 0 914 341" preserveAspectRatio="none" style={{position: 'absolute', inset: 0, width: '100%', height: '100%'}} aria-hidden="true">
        {inputRows.map((y, index) => <line key={`in-${index}`} x1="190" y1={y} x2="457" y2="170" stroke={color} strokeOpacity=".28" strokeWidth="1" pathLength="1" strokeDasharray="1" strokeDashoffset={1 - progress} />)}
        {scene.outputs.map((_, index) => <line key={`out-${index}`} x1="457" y1="170" x2="755" y2={115 + index * 110} stroke={color} strokeOpacity=".5" strokeWidth="1.5" pathLength="1" strokeDasharray="1" strokeDashoffset={1 - progress} />)}
      </svg>
      <div style={{zIndex: 1, display: 'grid', gridTemplateColumns: 'repeat(2,minmax(0,1fr))', gap: 8}}>
        {scene.inputs.map((input, index) => { const entered = enterAt(frame, fps, index, 12); return <span key={input} style={{...fadeStyle(entered, 8), minHeight: 38, display: 'grid', placeItems: 'center', padding: '6px 9px', border: '1px solid #313a34', background: '#0d120f', color: '#9ca49c', fontSize: 10, letterSpacing: .6, textAlign: 'center'}}>{input}</span>; })}
      </div>
      <div style={{zIndex: 2, width: 148, height: 148, margin: 'auto', display: 'grid', placeContent: 'center', padding: 20, transform: 'rotate(45deg)', border: `1px solid ${color}`, background: `radial-gradient(circle,${color}1a,#0b100d 72%)`, boxShadow: `0 0 35px ${color}17`}}><b style={{transform: 'rotate(-45deg)', color: '#eee7d3', font: '700 20px/1.05 Almendra, Georgia, serif', textAlign: 'center'}}>{scene.gate}</b></div>
      <div style={{zIndex: 1, display: 'grid', gap: 22}}>
        {scene.outputs.map((output, index) => { const entered = enterAt(frame, fps, index + scene.inputs.length, 12); return <div key={output} style={{...fadeStyle(entered, 8), minHeight: 65, display: 'grid', placeItems: 'center', padding: '12px', borderLeft: `3px solid ${color}`, background: `${color}0b`, color: '#eee7d3', font: '700 18px/1 Almendra, Georgia, serif', textAlign: 'center'}}>{output}</div>; })}
      </div>
    </div>
  );
};

const renderers = {flow: FlowDiagram, compare: CompareDiagram, boundary: BoundaryDiagram, timeline: TimelineDiagram, network: NetworkDiagram, stack: StackDiagram, funnel: FunnelDiagram};

export const SystemMap = ({visualizationId = 'what', color = '#d9f99d'}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const scene = getVisualization(visualizationId) || getVisualization('what');
  const Diagram = renderers[scene.type];
  const headerIn = enterAt(frame, fps, 0, 1);
  const footerIn = enterAt(frame, fps, 7, 25);
  const drift = interpolate(frame, [0, 359], [-8, 14], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

  return (
    <AbsoluteFill style={{background: '#080b09', color: '#eee7d3', overflow: 'hidden', fontFamily: 'IBM Plex Mono, monospace'}}>
      <div style={{position: 'absolute', inset: 0, opacity: .11, backgroundImage: `linear-gradient(${color}22 1px,transparent 1px),linear-gradient(90deg,${color}22 1px,transparent 1px)`, backgroundSize: '34px 34px'}} />
      <div style={{position: 'absolute', width: 590, height: 390, left: `${drift}%`, top: -210, borderRadius: '50%', background: `radial-gradient(circle,${color}1d,transparent 68%)`, filter: 'blur(8px)'}} />
      <header style={{...fadeStyle(headerIn, 10), position: 'absolute', zIndex: 4, left: 42, right: 42, top: 27, display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', paddingBottom: 13, borderBottom: '1px solid #29312c'}}>
        <div><span style={{display: 'block', marginBottom: 7, color, fontSize: 8, letterSpacing: 2.5}}>{typeLabels[scene.type]}</span><h2 style={{margin: 0, color: '#eee7d3', font: '700 27px/1 Almendra, Georgia, serif'}}>{scene.title}</h2></div>
        <span style={{color: '#5f6962', fontSize: 8, letterSpacing: 2}}>ILLUSTRATED ANSWER / {visualizationId.toUpperCase()}</span>
      </header>
      <Diagram scene={scene} frame={frame} fps={fps} color={color} />
      <footer style={{...fadeStyle(footerIn, 8), position: 'absolute', zIndex: 4, left: 42, right: 42, bottom: 17, display: 'grid', gridTemplateColumns: 'auto 1fr', alignItems: 'center', gap: 14}}>
        <span style={{color, fontSize: 10}}>ᚱ</span><p style={{margin: 0, color: '#768179', font: 'italic 14px/1 Newsreader, Georgia, serif'}}>{scene.takeaway}</p>
      </footer>
    </AbsoluteFill>
  );
};
