import React, {useEffect, useMemo, useRef, useState} from 'react';
import {Player} from '@remotion/player';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {SystemMap} from './SystemMap';
import {
  documents, entryQuestions, fileToId, getAdventureNode, getDocument, getQuestionsForDocument,
  getRegion, looseThreads, regions,
} from './docs';

const statusLabels = {implemented: 'Works today', mixed: 'Partly available', planned: 'Designed, not built yet'};
const depthLabels = ['', 'The Shore', 'The Trail', 'The Keep', 'The Underhall', 'The Runesmithy'];
const depthDescriptions = ['', 'Everyday outcome', 'What it means for you', 'How the pieces fit', 'Architecture and trust', 'Exact contracts and internals'];
const routeFromHash = () => {
  const match = window.location.hash.match(/^#\/doc\/([a-z-]+)/);
  return match ? {view: 'doc', id: match[1]} : {view: 'saga'};
};
const slugify = (value) => value.toLowerCase().replace(/[^a-z0-9\s-]/g, '').trim().replace(/\s+/g, '-');
const extractToc = (raw) => raw.split('\n').flatMap((line) => {
  const match = line.match(/^(##|###)\s+(.+)$/);
  return match ? [{level: match[1].length, label: match[2].replace(/[`*]/g, ''), id: slugify(match[2].replace(/[`*]/g, ''))}] : [];
});

const useVisited = () => {
  const [visited, setVisited] = useState(() => {
    try { return JSON.parse(localStorage.getItem('loreholm-atlas-visited') || '[]'); } catch { return []; }
  });
  const mark = (id) => setVisited((current) => {
    if (current.includes(id)) return current;
    const next = [...current, id];
    localStorage.setItem('loreholm-atlas-visited', JSON.stringify(next));
    return next;
  });
  return [visited, mark];
};

const Sigil = ({doc, small = false}) => (
  <span className={`sigil ${small ? 'sigil-small' : ''}`} style={{'--sigil': doc.color}} aria-hidden="true"><i>{doc.numeral}</i></span>
);
const Status = ({status}) => <span className={`status status-${status}`}><i />{statusLabels[status]}</span>;
const Depth = ({value}) => <span className="depth" aria-label={`Depth ${value} of 5`}>{[1, 2, 3, 4, 5].map((n) => <i className={n <= value ? 'lit' : ''} key={n} />)}</span>;

const Topbar = ({route, setRoute, onSearch, visited}) => (
  <header className="topbar">
    <button className="brand" onClick={() => setRoute({view: 'saga'})} aria-label="Return to the saga gate">
      <span className="brand-mark"><i>ᚺ</i></span>
      <span><b>LOREHOLM</b><small>THE LIVING SAGA</small></span>
    </button>
    <div className="topbar-actions">
      <div className="discovery"><span>{visited.length}</span> / {documents.length} TOMES OPENED</div>
      <button className="search-trigger" onClick={onSearch}><kbd>/</kbd> Ask the codex</button>
      {route.view === 'doc' && <button className="atlas-return" onClick={() => setRoute({view: 'saga'})}>Return to the fire</button>}
    </div>
  </header>
);

const FjordWindow = () => (
  <div className="fjord-window" aria-hidden="true">
    <div className="aurora aurora-one" /><div className="aurora aurora-two" />
    <svg viewBox="0 0 620 560" role="presentation">
      <path className="moon-ring" d="M430 51a112 112 0 1 1-49 212" />
      <g className="dragon-stars">
        <path d="M74 104l34-23 27 14 31-37 26 35 42-7 20 30-37-8-26 22-45-8-30 22-6-31z" />
        <circle cx="74" cy="104" r="2" /><circle cx="108" cy="81" r="2" /><circle cx="135" cy="95" r="2" /><circle cx="166" cy="58" r="2" /><circle cx="192" cy="93" r="2" /><circle cx="234" cy="86" r="2" /><circle cx="254" cy="116" r="2" />
      </g>
      <path className="far-mountain" d="M0 302l108-126 59 72 85-123 99 134 68-84 82 104 58-56 61 73v116H0z" />
      <path className="near-mountain" d="M0 350l87-87 56 56 79-93 83 96 52-55 85 75 70-82 108 106v90H0z" />
      <path className="fjord-water" d="M0 375c115-25 188 35 296 4 117-34 210-16 324 18v163H0z" />
      <g className="longship">
        <path d="M185 412c59 24 165 24 233 0l-26 38c-67 21-141 20-189 0z" />
        <path d="M202 419l-42-44 11-4 39 42z" />
        <path className="dragon-prow" d="M409 416l41-48 13 5-10 7 7 8-12-2-22 38z" />
        <path d="M300 413V294M301 305l92 76h-92z" />
        <path d="M301 305l-75 76h75z" />
        <circle cx="239" cy="427" r="8" /><circle cx="274" cy="432" r="8" /><circle cx="310" cy="433" r="8" /><circle cx="346" cy="430" r="8" /><circle cx="380" cy="424" r="8" />
      </g>
      <path className="water-rune" d="M40 490c100-24 173 27 269 3 103-26 176 20 271-7M89 523c96-19 137 18 218 3 100-20 167 13 244-4" />
    </svg>
    <div className="fjord-caption"><span>ᚠ ᚢ ᚦ ᚨ ᚱ ᚲ</span><p>The shortest path to lore begins with a question.</p></div>
  </div>
);

const Hero = ({choose}) => (
  <section className="saga-hero">
    <div className="hero-copy">
      <p className="eyebrow"><span /> A DEEP WORLD, ONE QUESTION AT A TIME</p>
      <h1>There is a lot<br />to <em>learn.</em></h1>
      <p className="hero-lede">Loreholm has deep systems beneath a simple promise: private memory that helps you understand your work. You do not need to learn it all at once. Choose what matters now, then follow the trail as far as your curiosity takes you.</p>
      <div className="entry-runes">
        {entryQuestions.map((entry) => (
          <button key={entry.next} onClick={() => choose(entry.next)}>
            <i><span>{entry.mark}</span></i><span><small>{entry.name}</small><b>{entry.label}</b></span><em>Choose ↘</em>
          </button>
        ))}
      </div>
    </div>
    <FjordWindow />
  </section>
);

const StoryProof = ({docId, setRoute, primary}) => {
  const doc = getDocument(docId);
  return (
    <button className={`story-proof ${primary ? 'primary' : ''}`} onClick={() => setRoute({view: 'doc', id: doc.id})}>
      <Sigil doc={doc} small /><span><small>{primary ? 'READ THE GUIDE BEHIND THIS ANSWER' : 'RELATED TECHNICAL GUIDE'}</small><b>{doc.title}</b></span><em>{doc.time} ↗</em>
    </button>
  );
};

const Adventure = ({trail, choose, back, reset, setRoute}) => {
  const currentId = trail[trail.length - 1];
  const node = getAdventureNode(currentId);
  const previousNode = trail.length > 1 ? getAdventureNode(trail[trail.length - 2]) : null;
  const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  if (!node) return (
    <section className="adventure empty-adventure" id="adventure">
      <p className="eyebrow"><span /> THE ORACLE FIRE WAITS</p>
      <h2>Begin with one question.<br /><em>The world deepens from there.</em></h2>
    </section>
  );
  return (
    <section className="adventure" id="adventure" style={{'--quest': getDocument(node.docs[0]).color}}>
      <div className="saga-trail">
        <span>YOUR SAGA</span>
        {trail.map((id, index) => <React.Fragment key={`${id}-${index}`}><i /> <button onClick={() => back(index)}>{getAdventureNode(id).eyebrow}</button></React.Fragment>)}
        <button className="reset-saga" onClick={reset}>Cast into the fire</button>
      </div>
      <div className="adventure-grid">
        <article className="oracle-card">
          <div className="oracle-rune">{entryQuestions.find((entry) => trail[0] === entry.next)?.mark || 'ᚱ'}</div>
          <div className="depth-marker">
            <span>DEPTH {node.depth} / 5</span>
            <div>{[1, 2, 3, 4, 5].map((level) => <i key={level} className={level <= node.depth ? 'reached' : ''} />)}</div>
            <b>{depthLabels[node.depth]}</b><small>{depthDescriptions[node.depth]}</small>
          </div>
          <p className="eyebrow"><span /> {node.eyebrow}</p>
          <h2>{node.question}</h2>
          <Status status={node.status} />
          <div className="oracle-answer">{node.answer.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}</div>
          <div className="proofs"><p>Want the source behind this answer?</p><StoryProof docId={node.docs[0]} setRoute={setRoute} primary />{node.depth >= 3 && node.docs.slice(1).map((id) => <StoryProof key={id} docId={id} setRoute={setRoute} />)}</div>
        </article>

        <div className="vision-panel">
          <div className="vision-corners" />
          <Player
            key={`${currentId}-${node.scene}`}
            component={SystemMap}
            inputProps={{scenario: node.scene}}
            durationInFrames={360}
            compositionWidth={1000}
            compositionHeight={480}
            fps={30}
            loop
            autoPlay={!reducedMotion}
            controls
            acknowledgeRemotionLicense
            style={{width: '100%', aspectRatio: '25 / 12'}}
          />
          <div className="vision-caption"><span>THE SEER'S POOL</span><p>This is how the idea would feel in use—not merely how packets travel.</p></div>
        </div>
      </div>
      <div className="next-questions">
        <button className="next-back" onClick={() => {
          if (previousNode) back(trail.length - 2);
          else { reset(); window.scrollTo({top: 0, behavior: 'smooth'}); }
        }}>
          <i>←</i>
          <span>
            <b>{previousNode ? 'Back to the previous answer' : 'Back to the beginning'}</b>
            <small>{previousNode ? previousNode.question : 'Choose another first question'}</small>
          </span>
          <em>↖</em>
        </button>
        {node.options.map((option, index) => {
          const targetDepth = getAdventureNode(option.next).depth;
          const direction = targetDepth > node.depth ? `Descend to ${depthLabels[targetDepth]}` : targetDepth < node.depth ? `Return to ${depthLabels[targetDepth]}` : `Explore ${depthLabels[targetDepth]}`;
          return <button key={option.next} onClick={() => choose(option.next)}><i>0{index + 1}</i><span><b>{option.label}</b><small>{direction}</small></span><em>→</em></button>;
        })}
      </div>
    </section>
  );
};

const LooseThreads = ({choose}) => (
  <section className="loose-threads">
    <header><p className="eyebrow"><span /> QUESTIONS HEARD IN THE LONGHOUSE</p><h2>Pull any loose thread.</h2><p>No prescribed order. Every question rejoins the larger saga.</p></header>
    <div className="thread-field">
      {looseThreads.map((id, index) => {
        const node = getAdventureNode(id);
        return <button key={id} className={`thread thread-${index + 1}`} onClick={() => choose(id)}><i>{String(index + 1).padStart(2, '0')}</i><span>{node.question}</span><em>{node.eyebrow} ↗</em></button>;
      })}
      <svg viewBox="0 0 1000 460" preserveAspectRatio="none" aria-hidden="true"><path d="M100 90C240 190 224 339 430 279S664 41 883 104M108 350c158-43 202-227 384-172s185 230 395 181M293 66c73 99 192 114 222 239s170 76 226 18" /></svg>
    </div>
  </section>
);

const CodexHall = ({visited, setRoute}) => {
  const [open, setOpen] = useState(false);
  return (
    <section className={`codex-hall ${open ? 'open' : ''}`}>
      <button className="codex-door" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <span className="door-rune">ᛟ</span><span><small>FOR CARTOGRAPHERS AND COMPLETIONISTS</small><b>{open ? 'Close the Codex Hall' : 'I still want the complete manual.'}</b></span><em>{open ? 'Seal ↑' : `${documents.length} source tomes ↓`}</em>
      </button>
      {open && <div className="codex-shelves">{regions.map((region) => (
        <section key={region.id}><header><i style={{background: region.color}} /><span><small>{region.kicker}</small><b>{region.name}</b></span></header>
          {documents.filter((doc) => doc.region === region.id).map((doc) => <button key={doc.id} onClick={() => setRoute({view: 'doc', id: doc.id})} className={visited.includes(doc.id) ? 'visited' : ''}><Sigil doc={doc} small /><span><b>{doc.title}</b><small>{doc.short}</small></span><Status status={doc.status} /></button>)}
        </section>
      ))}</div>}
    </section>
  );
};

const Saga = ({setRoute, visited, trail, choose, begin, back, reset}) => (
  <main className="saga">
    <Hero choose={begin} />
    <Adventure trail={trail} choose={choose} back={back} reset={reset} setRoute={setRoute} />
    <LooseThreads choose={choose} />
    <CodexHall visited={visited} setRoute={setRoute} />
    <footer className="saga-footer"><span className="brand-mark"><i>ᚺ</i></span><p>Context is the sea.<br />Evidence is the shore.</p><small>Every path distinguishes what works today from territory still hidden in mist.</small></footer>
  </main>
);

const CodeBlock = ({className, children, ...props}) => {
  const [copied, setCopied] = useState(false);
  const language = className?.replace('language-', '') || '';
  const text = String(children).replace(/\n$/, '');
  const isBlock = Boolean(className) || text.includes('\n');
  if (!isBlock) return <code className={className} {...props}>{children}</code>;
  const copy = async () => { await navigator.clipboard.writeText(text); setCopied(true); window.setTimeout(() => setCopied(false), 1200); };
  return <code className={className} {...props}>{language && <span className="code-language">{language}</span>}<button className="copy-code" onClick={copy}>{copied ? 'Copied' : 'Copy rune'}</button>{children}</code>;
};

const FieldSketch = ({children}) => {
  const child = React.Children.toArray(children)[0];
  if (child?.props?.className === 'language-text') {
    return <details className="field-sketch"><summary><span>ᚱ</span><b>Open the field sketch</b><em>A structural diagram lives here ↓</em></summary><pre>{children}</pre></details>;
  }
  return <pre>{children}</pre>;
};

const Heading = ({level, children}) => {
  const text = React.Children.toArray(children).join('');
  const id = slugify(text);
  const Tag = `h${level}`;
  return <Tag id={id}>{children}<button className="heading-anchor" onClick={() => document.getElementById(id)?.scrollIntoView({behavior: 'smooth', block: 'start'})} aria-label={`Scroll to ${text}`}>ᚱ</button></Tag>;
};

const Article = ({id, setRoute, visited, markVisited, ask}) => {
  const doc = getDocument(id) || documents[0];
  const region = getRegion(doc.region);
  const toc = useMemo(() => extractToc(doc.raw), [doc.raw]);
  const questions = getQuestionsForDocument(doc.id);
  const currentIndex = documents.findIndex((item) => item.id === doc.id);
  const next = documents[(currentIndex + 1) % documents.length];
  useEffect(() => { markVisited(doc.id); window.scrollTo({top: 0, behavior: 'auto'}); document.title = `${doc.title} — Loreholm Codex`; }, [doc.id]);
  const link = ({href = '', children, ...props}) => {
    const filename = href.split('/').pop()?.split('#')[0]?.toLowerCase();
    const mapped = fileToId[filename];
    if (mapped) return <a href={`#/doc/${mapped}`} onClick={(event) => {event.preventDefault(); setRoute({view: 'doc', id: mapped});}} {...props}>{children}</a>;
    const repositoryFiles = {readme: 'Readme.md', 'readme.md': 'Readme.md', 'security.md': 'SECURITY.md'};
    if (repositoryFiles[filename]) return <a href={`https://github.com/Loreholm/Loreholm/blob/v2/${repositoryFiles[filename]}${href.includes('#') ? `#${href.split('#')[1]}` : ''}`} target="_blank" rel="noreferrer" {...props}>{children}</a>;
    const external = /^https?:/.test(href);
    return <a href={href} target={external ? '_blank' : undefined} rel={external ? 'noreferrer' : undefined} {...props}>{children}</a>;
  };
  return (
    <main className="reader" style={{'--doc': doc.color}}>
      <aside className="reader-rail"><div className="rail-region"><span style={{background: region.color}} />{region.name}</div><nav className="mini-atlas" aria-label="Codex tomes">{documents.map((item) => <button key={item.id} className={`${item.id === doc.id ? 'active' : ''} ${visited.includes(item.id) ? 'visited' : ''}`} onClick={() => setRoute({view: 'doc', id: item.id})}><span>{item.numeral}</span><b>{item.title}</b><i /></button>)}</nav></aside>
      <article className="document">
        <header className="document-cover">
          <div className="document-number">CODEX TOME / {doc.numeral}</div>
          <div className="document-title-row"><Sigil doc={doc} /><div><p>{region.name}</p><h1>{doc.title}</h1></div></div>
          <p className="document-summary">{doc.summary}</p>
          <div className="document-stats"><Status status={doc.status} /><span>{doc.time} read</span><span>Depth <Depth value={doc.depth} /></span></div>
          <div className="guide-questions"><small>TRAVELERS OPEN THIS TOME TO ASK</small>{questions.slice(0, 3).map((item) => <button key={item.id} onClick={() => ask(item.id)}>{item.question}<span>Follow this question ↗</span></button>)}</div>
        </header>
        <div className="article-grid"><div className="markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{h1: () => <></>, h2: ({children}) => <Heading level={2}>{children}</Heading>, h3: ({children}) => <Heading level={3}>{children}</Heading>, a: link, code: CodeBlock, pre: FieldSketch, table: ({children}) => <div className="table-scroll"><table>{children}</table></div>}}>{doc.raw}</ReactMarkdown></div>
          <aside className="toc"><p>RUNES IN THIS TOME</p>{toc.map((item) => <button key={`${item.id}-${item.level}`} className={`toc-${item.level}`} onClick={() => document.getElementById(item.id)?.scrollIntoView({behavior: 'smooth', block: 'start'})}>{item.label}</button>)}<div className="toc-legend"><Status status={doc.status} /><p>{doc.status === 'planned' ? 'Accepted lore. Not executable yet.' : doc.status === 'mixed' ? 'Some shores remain in mist.' : 'Grounded in current Loreholm code.'}</p></div></aside>
        </div>
        <footer className="next-guide"><div><p>ANOTHER PATH THROUGH THE SAGA</p><h2>{questions[0]?.question || next.title}</h2><span>Return to the fire and follow the question instead of reading in file order.</span></div><button onClick={() => questions[0] ? ask(questions[0].id) : setRoute({view: 'doc', id: next.id})}><span className="door-rune">ᚱ</span><span>Follow the question<br /><b>Back to the saga ↗</b></span></button></footer>
      </article>
    </main>
  );
};

const Search = ({open, setOpen, setRoute, ask}) => {
  const [query, setQuery] = useState('');
  const inputRef = useRef(null);
  useEffect(() => { if (open) window.setTimeout(() => inputRef.current?.focus(), 20); else setQuery(''); }, [open]);
  const results = useMemo(() => {
    const q = query.toLowerCase().trim();
    if (!q) return documents.slice(0, 5);
    return documents.map((doc) => ({...doc, score: `${doc.title} ${doc.summary} ${doc.raw}`.toLowerCase().indexOf(q)})).filter((doc) => doc.score >= 0).sort((a, b) => a.score - b.score).slice(0, 8);
  }, [query]);
  if (!open) return null;
  return <div className="search-overlay" role="dialog" aria-modal="true" aria-label="Ask the codex" onMouseDown={(event) => {if (event.target === event.currentTarget) setOpen(false);}}><div className="search-panel"><div className="search-input"><span>ᚱ</span><input ref={inputRef} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Ask about a boundary, memory, or promise…" /><kbd>ESC</kbd></div><p className="search-label">{query ? `${results.length} TOMES ANSWER` : 'THE CODEX SUGGESTS'}</p><div className="search-results">{results.map((doc) => <button key={doc.id} onClick={() => {setRoute({view: 'doc', id: doc.id}); setOpen(false);}}><Sigil doc={doc} small /><span><b>{doc.title}</b><small>{doc.summary}</small></span><em>{doc.numeral} ↗</em></button>)}{!results.length && <div className="no-results"><b>The runes are quiet.</b><span>Try: capture, tunnel, evidence, identity, deletion.</span></div>}</div></div></div>;
};

export const App = () => {
  const [route, setRouteState] = useState(routeFromHash);
  const [searchOpen, setSearchOpen] = useState(false);
  const [visited, markVisited] = useVisited();
  const [trail, setTrail] = useState([]);
  const setRoute = (next) => { window.location.hash = next.view === 'doc' ? `#/doc/${next.id}` : '#/'; setRouteState(next); if (next.view === 'saga') window.scrollTo({top: 0, behavior: 'auto'}); };
  const choose = (id) => { setTrail((current) => [...current, id]); window.setTimeout(() => document.getElementById('adventure')?.scrollIntoView({behavior: 'smooth'}), 30); };
  const begin = (id) => { setTrail([id]); window.setTimeout(() => document.getElementById('adventure')?.scrollIntoView({behavior: 'smooth'}), 30); };
  const ask = (id) => { setTrail([id]); setRoute({view: 'saga'}); window.setTimeout(() => document.getElementById('adventure')?.scrollIntoView({behavior: 'smooth'}), 50); };
  useEffect(() => {
    const change = () => setRouteState(routeFromHash());
    const key = (event) => { if (event.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) {event.preventDefault(); setSearchOpen(true);} if (event.key === 'Escape') setSearchOpen(false); };
    window.addEventListener('hashchange', change); window.addEventListener('keydown', key);
    return () => {window.removeEventListener('hashchange', change); window.removeEventListener('keydown', key);};
  }, []);
  useEffect(() => { if (route.view === 'saga') document.title = 'Loreholm — The Living Saga'; }, [route.view]);
  return <div className="site-shell"><div className="grain" /><Topbar route={route} setRoute={setRoute} onSearch={() => setSearchOpen(true)} visited={visited} />{route.view === 'doc' ? <Article id={route.id} setRoute={setRoute} visited={visited} markVisited={markVisited} ask={ask} /> : <Saga setRoute={setRoute} visited={visited} trail={trail} choose={choose} begin={begin} back={(index) => setTrail((current) => current.slice(0, index + 1))} reset={() => setTrail([])} />}<Search open={searchOpen} setOpen={setSearchOpen} setRoute={setRoute} ask={ask} /></div>;
};
