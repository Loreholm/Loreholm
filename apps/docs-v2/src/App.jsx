import React, {useEffect, useMemo, useRef, useState} from 'react';
import {Player} from '@remotion/player';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {SystemMap} from './SystemMap';
import {documents, fileToId, getDocument, getRegion, paths, regions} from './docs';

const statusLabels = {implemented: 'In the world', mixed: 'Partly charted', planned: 'Beyond the fog'};

const routeFromHash = () => {
  const match = window.location.hash.match(/^#\/doc\/([a-z-]+)/);
  return match ? {view: 'doc', id: match[1]} : {view: 'atlas'};
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
  <span className={`sigil ${small ? 'sigil-small' : ''}`} style={{'--sigil': doc.color}} aria-hidden="true">
    <i>{doc.numeral}</i>
  </span>
);

const Status = ({status}) => <span className={`status status-${status}`}><i />{statusLabels[status]}</span>;

const Depth = ({value}) => (
  <span className="depth" aria-label={`Depth ${value} of 5`}>
    {[1, 2, 3, 4, 5].map((n) => <i className={n <= value ? 'lit' : ''} key={n} />)}
  </span>
);

const Topbar = ({route, setRoute, onSearch, visited}) => (
  <header className="topbar">
    <button className="brand" onClick={() => setRoute({view: 'atlas'})} aria-label="Return to the atlas">
      <span className="brand-mark"><i>L</i></span>
      <span><b>LOREHOLM</b><small>V2 / FIELD ATLAS</small></span>
    </button>
    <div className="topbar-actions">
      <div className="discovery"><span>{visited.length}</span> / {documents.length} DISCOVERED</div>
      <button className="search-trigger" onClick={onSearch}><kbd>/</kbd> Search the archive</button>
      {route.view === 'doc' && <button className="atlas-return" onClick={() => setRoute({view: 'atlas'})}>View atlas</button>}
    </div>
  </header>
);

const Trail = ({path, setRoute, visited}) => (
  <article className="trail-card" style={{'--trail': path.color}}>
    <p>{path.eyebrow}</p>
    <h3>{path.title}</h3>
    <span className="trail-time">{path.duration}</span>
    <div className="trail-line">
      {path.docs.map((id, index) => {
        const doc = getDocument(id);
        return (
          <React.Fragment key={id}>
            {index > 0 && <i className="trail-join" />}
            <button className={visited.includes(id) ? 'visited' : ''} onClick={() => setRoute({view: 'doc', id})} title={doc.title}>
              <span>{doc.numeral}</span>
            </button>
          </React.Fragment>
        );
      })}
    </div>
    <p className="trail-description">{path.description}</p>
    <button className="trail-start" onClick={() => setRoute({view: 'doc', id: path.docs[0]})}>Begin journey <span>↗</span></button>
  </article>
);

const AtlasNode = ({doc, visited, setRoute}) => (
  <button className={`atlas-node ${visited ? 'visited' : ''}`} style={{'--node': doc.color}} onClick={() => setRoute({view: 'doc', id: doc.id})}>
    <Sigil doc={doc} />
    <span className="node-copy">
      <small>{doc.numeral} · {doc.time}</small>
      <b>{doc.title}</b>
      <em>{doc.short}</em>
      <span className="node-meta"><Status status={doc.status} /><Depth value={doc.depth} /></span>
    </span>
    <span className="node-arrow">↗</span>
  </button>
);

const Atlas = ({setRoute, visited}) => {
  const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  return (
    <main className="atlas">
      <section className="hero">
        <div className="hero-copy">
          <p className="eyebrow"><span /> THE LIBRARIAN'S MAP / REVISION 02</p>
          <h1>A small door<br />into a <em>deep world.</em></h1>
          <p className="hero-lede">Loreholm captures the context around your work, keeps it in your private instance, and turns it into knowledge with an evidence trail. Start with the fire. Follow the signal. Descend when you're ready.</p>
          <div className="hero-actions">
            <button className="primary-action" onClick={() => setRoute({view: 'doc', id: 'architecture'})}>Enter the atlas <span>↘</span></button>
            <button className="quiet-action" onClick={() => document.getElementById('paths')?.scrollIntoView({behavior: 'smooth'})}>Choose a path</button>
          </div>
          <div className="world-state">
            <span><i className="live" /> CAPTURE FOUNDATION LIVE</span>
            <span><i /> MINING TERRITORY PLANNED</span>
          </div>
        </div>
        <div className="map-player-wrap">
          <div className="map-frame-corners" />
          <Player
            component={SystemMap}
            durationInFrames={360}
            compositionWidth={960}
            compositionHeight={400}
            fps={30}
            loop
            autoPlay={!reducedMotion}
            controls
            acknowledgeRemotionLicense
            style={{width: '100%', aspectRatio: '12 / 5'}}
          />
          <div className="map-caption"><span>01</span> THE SIGNAL PATH <i>Animation powered by Remotion</i></div>
        </div>
      </section>

      <section className="paths-section" id="paths">
        <header className="section-heading">
          <div><p className="eyebrow"><span /> SELECT A QUESTLINE</p><h2>There is no wrong first step.</h2></div>
          <p>Each path is a curated route through the same world. The deeper systems will still be there when you return.</p>
        </header>
        <div className="trails">{paths.map((path) => <Trail key={path.id} path={path} setRoute={setRoute} visited={visited} />)}</div>
      </section>

      <section className="world-section">
        <header className="section-heading">
          <div><p className="eyebrow"><span /> COMPLETE WORLD MAP</p><h2>Five regions. Twelve field guides.</h2></div>
          <p>Depth measures conceptual density, not importance. Status tells you what exists in code and what remains accepted design.</p>
        </header>
        <div className="regions">
          {regions.map((region, regionIndex) => (
            <section className="region" key={region.id} style={{'--region': region.color}}>
              <header><span>0{regionIndex + 1}</span><div><small>{region.kicker}</small><h3>{region.name}</h3></div><i /></header>
              <div className="region-nodes">
                {documents.filter((doc) => doc.region === region.id).map((doc) => <AtlasNode key={doc.id} doc={doc} visited={visited.includes(doc.id)} setRoute={setRoute} />)}
              </div>
            </section>
          ))}
        </div>
      </section>

      <section className="atlas-footer">
        <div><span className="brand-mark"><i>L</i></span><p>Context is raw.<br />Knowledge is earned.</p></div>
        <p>V2 documentation is generated from the repository's Markdown source. Planned systems are marked as terrain—not promises already shipped.</p>
      </section>
    </main>
  );
};

const CodeBlock = ({className, children, ...props}) => {
  const [copied, setCopied] = useState(false);
  const language = className?.replace('language-', '') || '';
  const text = String(children).replace(/\n$/, '');
  const isBlock = Boolean(className) || text.includes('\n');
  if (!isBlock) return <code className={className} {...props}>{children}</code>;
  const copy = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };
  return (
    <code className={className} {...props}>
      {language && <span className="code-language">{language}</span>}
      <button className="copy-code" onClick={copy}>{copied ? 'Copied' : 'Copy'}</button>
      {children}
    </code>
  );
};

const Heading = ({level, children}) => {
  const text = React.Children.toArray(children).join('');
  const id = slugify(text);
  const Tag = `h${level}`;
  const reveal = () => document.getElementById(id)?.scrollIntoView({behavior: 'smooth', block: 'start'});
  return <Tag id={id}>{children}<button className="heading-anchor" onClick={reveal} aria-label={`Scroll to ${text}`}>#</button></Tag>;
};

const Article = ({id, setRoute, visited, markVisited}) => {
  const doc = getDocument(id) || documents[0];
  const region = getRegion(doc.region);
  const toc = useMemo(() => extractToc(doc.raw), [doc.raw]);
  const articleRef = useRef(null);
  const currentIndex = documents.findIndex((item) => item.id === doc.id);
  const next = documents[(currentIndex + 1) % documents.length];

  useEffect(() => {
    markVisited(doc.id);
    window.scrollTo({top: 0, behavior: 'auto'});
    document.title = `${doc.title} — Loreholm Atlas`;
  }, [doc.id]);

  const link = ({href = '', children, ...props}) => {
    const filename = href.split('/').pop()?.split('#')[0]?.toLowerCase();
    const mapped = fileToId[filename];
    if (mapped) return <a href={`#/doc/${mapped}`} onClick={(event) => {event.preventDefault(); setRoute({view: 'doc', id: mapped});}} {...props}>{children}</a>;
    const repositoryFiles = {readme: 'Readme.md', 'readme.md': 'Readme.md', 'security.md': 'SECURITY.md'};
    if (repositoryFiles[filename]) {
      const source = `https://github.com/Loreholm/Loreholm/blob/v2/${repositoryFiles[filename]}${href.includes('#') ? `#${href.split('#')[1]}` : ''}`;
      return <a href={source} target="_blank" rel="noreferrer" {...props}>{children}</a>;
    }
    const external = /^https?:/.test(href);
    return <a href={href} target={external ? '_blank' : undefined} rel={external ? 'noreferrer' : undefined} {...props}>{children}</a>;
  };

  return (
    <main className="reader" style={{'--doc': doc.color}}>
      <aside className="reader-rail">
        <div className="rail-region"><span style={{background: region.color}} />{region.name}</div>
        <nav className="mini-atlas" aria-label="Documentation chapters">
          {documents.map((item) => (
            <button key={item.id} className={`${item.id === doc.id ? 'active' : ''} ${visited.includes(item.id) ? 'visited' : ''}`} onClick={() => setRoute({view: 'doc', id: item.id})}>
              <span>{item.numeral}</span><b>{item.title}</b><i />
            </button>
          ))}
        </nav>
      </aside>

      <article className="document" ref={articleRef}>
        <header className="document-cover">
          <div className="document-number">FIELD GUIDE / {doc.numeral}</div>
          <div className="document-title-row"><Sigil doc={doc} /><div><p>{region.name}</p><h1>{doc.title}</h1></div></div>
          <p className="document-summary">{doc.summary}</p>
          <div className="document-stats"><Status status={doc.status} /><span>{doc.time} read</span><span>Depth <Depth value={doc.depth} /></span></div>
        </header>

        <div className="article-grid">
          <div className="markdown-body">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                h1: ({children}) => <>{/* Cover supplies the title. */}</>,
                h2: ({children}) => <Heading level={2}>{children}</Heading>,
                h3: ({children}) => <Heading level={3}>{children}</Heading>,
                a: link,
                code: CodeBlock,
                table: ({children}) => <div className="table-scroll"><table>{children}</table></div>,
              }}
            >{doc.raw}</ReactMarkdown>
          </div>
          <aside className="toc">
            <p>IN THIS GUIDE</p>
            {toc.map((item) => <button key={`${item.id}-${item.level}`} className={`toc-${item.level}`} onClick={() => document.getElementById(item.id)?.scrollIntoView({behavior: 'smooth', block: 'start'})}>{item.label}</button>)}
            <div className="toc-legend"><Status status={doc.status} /><p>{doc.status === 'planned' ? 'Accepted design. Not executable yet.' : doc.status === 'mixed' ? 'Read section status carefully.' : 'Grounded in the current V2 code.'}</p></div>
          </aside>
        </div>

        <footer className="next-guide">
          <div><p>THE PATH CONTINUES</p><h2>{next.title}</h2><span>{next.summary}</span></div>
          <button onClick={() => setRoute({view: 'doc', id: next.id})}><Sigil doc={next} small /><span>Open field guide<br /><b>{next.numeral} ↗</b></span></button>
        </footer>
      </article>
    </main>
  );
};

const Search = ({open, setOpen, setRoute}) => {
  const [query, setQuery] = useState('');
  const inputRef = useRef(null);
  useEffect(() => { if (open) window.setTimeout(() => inputRef.current?.focus(), 20); else setQuery(''); }, [open]);
  const results = useMemo(() => {
    const q = query.toLowerCase().trim();
    if (!q) return documents.slice(0, 5);
    return documents.map((doc) => {
      const haystack = `${doc.title} ${doc.summary} ${doc.raw}`.toLowerCase();
      const index = haystack.indexOf(q);
      return {...doc, score: index === -1 ? 999999 : index};
    }).filter((doc) => doc.score < 999999).sort((a, b) => a.score - b.score).slice(0, 8);
  }, [query]);
  if (!open) return null;
  return (
    <div className="search-overlay" role="dialog" aria-modal="true" aria-label="Search documentation" onMouseDown={(event) => {if (event.target === event.currentTarget) setOpen(false);}}>
      <div className="search-panel">
        <div className="search-input"><span>⌕</span><input ref={inputRef} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search a concept, endpoint, or boundary…" /><kbd>ESC</kbd></div>
        <p className="search-label">{query ? `${results.length} SIGNALS FOUND` : 'SUGGESTED ENTRY POINTS'}</p>
        <div className="search-results">
          {results.map((doc) => <button key={doc.id} onClick={() => {setRoute({view: 'doc', id: doc.id}); setOpen(false);}}><Sigil doc={doc} small /><span><b>{doc.title}</b><small>{doc.summary}</small></span><em>{doc.numeral} ↗</em></button>)}
          {!results.length && <div className="no-results"><b>The archive is quiet.</b><span>Try a broader phrase: capture, tunnel, policy, evidence, backup.</span></div>}
        </div>
      </div>
    </div>
  );
};

export const App = () => {
  const [route, setRouteState] = useState(routeFromHash);
  const [searchOpen, setSearchOpen] = useState(false);
  const [visited, markVisited] = useVisited();

  const setRoute = (next) => {
    window.location.hash = next.view === 'doc' ? `#/doc/${next.id}` : '#/';
    setRouteState(next);
  };

  useEffect(() => {
    const change = () => setRouteState(routeFromHash());
    const key = (event) => {
      if (event.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) {event.preventDefault(); setSearchOpen(true);}
      if (event.key === 'Escape') setSearchOpen(false);
    };
    window.addEventListener('hashchange', change);
    window.addEventListener('keydown', key);
    return () => {window.removeEventListener('hashchange', change); window.removeEventListener('keydown', key);};
  }, []);

  useEffect(() => { if (route.view === 'atlas') document.title = 'Loreholm Atlas — V2 Documentation'; }, [route.view]);

  return (
    <div className="site-shell">
      <div className="grain" />
      <Topbar route={route} setRoute={setRoute} onSearch={() => setSearchOpen(true)} visited={visited} />
      {route.view === 'doc' ? <Article id={route.id} setRoute={setRoute} visited={visited} markVisited={markVisited} /> : <Atlas setRoute={setRoute} visited={visited} />}
      <Search open={searchOpen} setOpen={setSearchOpen} setRoute={setRoute} />
    </div>
  );
};
