import architecture from '../../../docs/01_Architecture.md?raw';
import networking from '../../../docs/02_Networking.md?raw';
import capture from '../../../docs/03_CaptureAPI.md?raw';
import operations from '../../../docs/04_InstanceOperations.md?raw';
import policy from '../../../docs/05_PolicyAndModels.md?raw';
import clients from '../../../docs/06_ClientsAndSpine.md?raw';
import mining from '../../../docs/07_MiningAndKnowledge.md?raw';
import lifecycle from '../../../docs/08_DataLifecycle.md?raw';
import chat from '../../../docs/09_Chat.md?raw';
import security from '../../../docs/13_SecurityModel.md?raw';
import development from '../../../docs/V2-Development.md?raw';
import decisions from '../../../notes/Architecture-Decisions.md?raw';

export const regions = [
  {id: 'threshold', name: 'The Threshold', kicker: 'Enter here', color: '#d9f99d'},
  {id: 'boundary', name: 'The Outer Wall', kicker: 'Trust & transport', color: '#66d6cf'},
  {id: 'instance', name: 'The Hearth', kicker: 'Run your world', color: '#f4b860'},
  {id: 'depths', name: 'The Deep Archive', kicker: 'Mine the meaning', color: '#e87951'},
  {id: 'workshop', name: 'The Workshop', kicker: 'Build & extend', color: '#a9a6ff'},
];

export const documents = [
  {
    id: 'architecture', file: '01_Architecture.md', numeral: 'I', title: 'Architecture',
    short: 'See the whole machine before opening it.', region: 'threshold', status: 'mixed', depth: 1,
    time: '8 min', color: '#d9f99d', raw: architecture,
    summary: 'The V2 authority shift, implemented capture foundation, retained tunnel, and planned mining pipeline.',
  },
  {
    id: 'networking', file: '02_Networking.md', numeral: 'II', title: 'Private Network',
    short: 'Walk the path from the front door to local data.', region: 'boundary', status: 'implemented', depth: 2,
    time: '6 min', color: '#66d6cf', raw: networking,
    summary: 'The front door, Headscale/Tailscale tunnel, endpoint shim, and container isolation invariants.',
  },
  {
    id: 'capture', file: '03_CaptureAPI.md', numeral: 'III', title: 'Capture Contract',
    short: 'Learn the language spoken by every observer.', region: 'instance', status: 'implemented', depth: 2,
    time: '10 min', color: '#f4b860', raw: capture,
    summary: 'Envelope fields, authentication, receipts, retries, timestamps, snapshots, and quarantine.',
  },
  {
    id: 'operations', file: '04_InstanceOperations.md', numeral: 'IV', title: 'Instance Operations',
    short: 'Raise, inspect, update, and stop an instance.', region: 'instance', status: 'implemented', depth: 2,
    time: '9 min', color: '#ffd38a', raw: operations,
    summary: 'Installation, containers, credentials, configuration, health, updates, and destructive removal.',
  },
  {
    id: 'policy', file: '05_PolicyAndModels.md', numeral: 'V', title: 'Policy & Models',
    short: 'Set the laws that govern capture and egress.', region: 'instance', status: 'mixed', depth: 3,
    time: '9 min', color: '#efad67', raw: policy,
    summary: 'Capture classes, remote processing modes, Bifrost routing, model roles, and planned budgets.',
  },
  {
    id: 'clients', file: '06_ClientsAndSpine.md', numeral: 'VI', title: 'Clients & Spine',
    short: 'Design a sensor that never becomes the judge.', region: 'workshop', status: 'planned', depth: 3,
    time: '7 min', color: '#a9a6ff', raw: clients,
    summary: 'Adapter responsibilities, embedded spine, offline queues, policy refresh, and revocation.',
  },
  {
    id: 'mining', file: '07_MiningAndKnowledge.md', numeral: 'VII', title: 'Mining & Knowledge',
    short: 'Descend into identity, evidence, and time.', region: 'depths', status: 'planned', depth: 5,
    time: '14 min', color: '#e87951', raw: mining,
    summary: 'Salience, mining stages, entity resolution, claims, provenance, vectors, schema, and surfacing.',
  },
  {
    id: 'lifecycle', file: '08_DataLifecycle.md', numeral: 'VIII', title: 'Data Lifecycle',
    short: 'Understand what can be kept, erased, restored, or shared.', region: 'depths', status: 'planned', depth: 4,
    time: '9 min', color: '#df8b6c', raw: lifecycle,
    summary: 'Retention, grace-period deletion, inference preservation, backups, restore, and disclosure.',
  },
  {
    id: 'chat', file: '09_Chat.md', numeral: 'IX', title: 'Browser Chat',
    short: 'Follow the first complete V2 expedition.', region: 'threshold', status: 'implemented', depth: 1,
    time: '6 min', color: '#bfe886', raw: chat,
    summary: 'OIDC, front-door relay, private streaming, Bifrost inference, and passive transcript capture.',
  },
  {
    id: 'security', file: '13_SecurityModel.md', numeral: 'X', title: 'Security Model',
    short: 'Inspect every gate and every promise.', region: 'boundary', status: 'mixed', depth: 4,
    time: '9 min', color: '#7dded7', raw: security,
    summary: 'Local and tunnel boundaries, credential separation, model egress, deletion limits, and verification.',
  },
  {
    id: 'development', file: 'V2-Development.md', numeral: 'XI', title: 'Development Stack',
    short: 'Enter the workshop with the right instruments.', region: 'workshop', status: 'implemented', depth: 3,
    time: '6 min', color: '#b8b5ff', raw: development,
    summary: 'Local Compose development, vLLM GPU overlay, model constraints, and browser-chat networking.',
  },
  {
    id: 'decisions', file: 'Architecture-Decisions.md', numeral: 'XII', title: 'Architecture Decisions',
    short: 'Read the strata beneath the current design.', region: 'depths', status: 'mixed', depth: 5,
    time: '30 min', color: '#ee906b', raw: decisions,
    summary: 'The chronological design record: settled boundaries, rejected alternatives, open questions, and V2 direction.',
  },
];

export const entryQuestions = [
  {name: 'Rune of Memory', label: 'How can it remember my work without making me stop and file things?', next: 'passive', mark: 'ᛗ'},
  {name: 'Rune of the Wall', label: 'Where does my private context actually go?', next: 'privacy', mark: 'ᛉ'},
  {name: 'Rune of Lore', label: 'How does a pile of activity become a useful answer?', next: 'knowledge', mark: 'ᚨ'},
  {name: 'Rune of Making', label: 'What would it take to connect my own tool?', next: 'builder', mark: 'ᛏ'},
];

export const adventureNodes = {
  passive: {
    eyebrow: 'Passive memory', question: 'Does Loreholm wait for me to tell it what matters?',
    answer: [
      'No. V2 moves the burden away from explicit “store this” calls. An adapter observes useful context, the embedded spine normalizes it, and your private instance durably captures the complete envelope.',
      'That capture is not yet knowledge. The instance—not the client—later decides what deserves to become an entity, claim, relationship, or episode.',
    ],
    status: 'mixed', docs: ['architecture', 'capture', 'chat'], scene: 'recall',
    options: [
      {label: 'What is inside one captured moment?', next: 'envelope'},
      {label: 'What if I work while disconnected?', next: 'offline'},
      {label: 'Show me how this helps future me.', next: 'recall'},
    ],
  },
  envelope: {
    eyebrow: 'The raw material', question: 'What does Loreholm actually capture?',
    answer: [
      'A capture is a versioned envelope: stable capture ID, device, source, two timestamps, policy context, capture class, and the source payload. Event streams and object snapshots use the same contract.',
      'The receipt means the instance durably staged that envelope. It does not mean the system has already interpreted or endorsed its contents.',
    ],
    status: 'implemented', docs: ['capture'], scene: 'builder',
    options: [
      {label: 'Who decides what becomes graph knowledge?', next: 'mining'},
      {label: 'Can an observer write directly to my graph?', next: 'adapter'},
      {label: 'Can I control what leaves the instance?', next: 'egress'},
    ],
  },
  offline: {
    eyebrow: 'Interrupted passage', question: 'Do I lose context when the tunnel is unavailable?',
    answer: [
      'The accepted client design queues normalized envelopes locally and retries them with the same capture IDs. The instance’s idempotency contract makes the replay safe instead of creating duplicate memory.',
      'The server-side receipt and deduplication foundation exists. The reusable embedded spine and its offline queue are still planned terrain.',
    ],
    status: 'mixed', docs: ['clients', 'capture'], scene: 'offline',
    options: [
      {label: 'How does the private tunnel work?', next: 'privacy'},
      {label: 'How would I build an observer?', next: 'builder'},
      {label: 'What is implemented right now?', next: 'operations'},
    ],
  },
  recall: {
    eyebrow: 'The future payoff', question: 'How could a captured decision help me three weeks later?',
    answer: [
      'Imagine saying, “Keep embeddings local; privacy matters more than the small accuracy gain.” The chat transcript is captured now. Later, the planned miner can extract the decision, reason, time, and evidence span.',
      'When future-you asks why the model stayed local, Loreholm can answer from the claim and show the exact source behind it. Capture exists today; that evidence-backed recall loop is the destination, not a shipped promise.',
    ],
    status: 'mixed', docs: ['chat', 'mining', 'architecture'], scene: 'recall',
    options: [
      {label: 'How can I trust the answer?', next: 'evidence'},
      {label: 'How does it recognize the same person twice?', next: 'identity'},
      {label: 'Can I remove the original conversation?', next: 'deletion'},
    ],
  },
  privacy: {
    eyebrow: 'The private boundary', question: 'Does using Loreholm put my raw context in the cloud?',
    answer: [
      'The public front door handles identity and permitted relay. It reaches your containerized instance through a Headscale-managed Tailscale tunnel. Durable captures, ArcadeDB, and Bifrost remain in your local instance.',
      'The tunnel is a passage to your application boundary—not a way to expose the database, model server, Docker socket, or host.',
    ],
    status: 'implemented', docs: ['networking', 'security'], scene: 'privacy',
    options: [
      {label: 'What can the front door actually see?', next: 'cloud'},
      {label: 'Can model processing leave my instance?', next: 'egress'},
      {label: 'What happens when I delete something?', next: 'deletion'},
    ],
  },
  cloud: {
    eyebrow: 'The public edge', question: 'What is the front door allowed to do?',
    answer: [
      'It can authenticate a person and relay narrowly allowed application traffic to the Tailnet endpoint shim. The shim forwards to the instance API on the private Compose bridge.',
      'It is not the authority for graph writes, capture policy, raw retention, ArcadeDB administration, or Bifrost. Those decisions remain inside the user-owned instance.',
    ],
    status: 'implemented', docs: ['networking', 'security', 'chat'], scene: 'privacy',
    options: [
      {label: 'Walk me through browser chat.', next: 'recall'},
      {label: 'How are credentials separated?', next: 'operations'},
      {label: 'What is still only a security promise?', next: 'deletion'},
    ],
  },
  egress: {
    eyebrow: 'Model egress', question: 'Can raw captures be sent to a remote model?',
    answer: [
      'Instance policy owns that choice. The accepted modes range from local-only processing to explicitly allowed remote processing, with capture classes and model roles evaluated before egress.',
      'Bifrost provides the routing seam. Complete budget enforcement and every planned egress control are not implemented yet, so the field guide separates present behavior from accepted design.',
    ],
    status: 'mixed', docs: ['policy', 'security'], scene: 'privacy',
    options: [
      {label: 'How is local-only enforced?', next: 'privacy'},
      {label: 'What does the miner need a model for?', next: 'mining'},
      {label: 'Show me the exact policy surface.', next: 'contract'},
    ],
  },
  deletion: {
    eyebrow: 'Forgetting', question: 'If I delete a capture, does every inference disappear too?',
    answer: [
      'The accepted lifecycle distinguishes source deletion from knowledge that may have independent evidence. A grace period permits recovery; hard deletion removes the capture and its evidence links.',
      'A claim may survive only when other evidence still supports it, and the provenance must change accordingly. This lifecycle is designed but not implemented in the current capture foundation.',
    ],
    status: 'planned', docs: ['lifecycle', 'security', 'mining'], scene: 'evidence',
    options: [
      {label: 'How is surviving knowledge justified?', next: 'evidence'},
      {label: 'What belongs in a real backup?', next: 'operations'},
      {label: 'Return to passive capture.', next: 'passive'},
    ],
  },
  knowledge: {
    eyebrow: 'From exhaust to meaning', question: 'Why not just search the raw logs?',
    answer: [
      'Raw captures tell you what happened; they do not reliably tell you who is the same person, which statement is a durable claim, when it was true, or what evidence supports it.',
      'The planned miner converts selected context into temporal, provenance-backed knowledge while preserving the source trail. That is the difference between retrieval and memory you can interrogate.',
    ],
    status: 'planned', docs: ['mining', 'architecture'], scene: 'evidence',
    options: [
      {label: 'What are the mining stages?', next: 'mining'},
      {label: 'How does identity resolution work?', next: 'identity'},
      {label: 'What makes an answer trustworthy?', next: 'evidence'},
    ],
  },
  mining: {
    eyebrow: 'The quiet librarian', question: 'Who turns context into graph knowledge?',
    answer: [
      'Only the instance miner is allowed to make that commitment. It interprets captures, checks salience, resolves identities, forms claims and episodes, attaches evidence, and commits an idempotent result.',
      'This pipeline is accepted V2 design and remains planned. Clients stay deliberately less powerful: they observe and deliver, but never decide graph truth.',
    ],
    status: 'planned', docs: ['mining', 'decisions'], scene: 'evidence',
    options: [
      {label: 'How are two mentions joined?', next: 'identity'},
      {label: 'How does evidence survive re-mining?', next: 'evidence'},
      {label: 'Which model is allowed to do this?', next: 'egress'},
    ],
  },
  identity: {
    eyebrow: 'Names are not people', question: 'How does Loreholm know two mentions refer to the same entity?',
    answer: [
      'It does not equate matching text with identity. The accepted design uses normalized aliases, context, candidate retrieval, conservative matching, and merge records so uncertain mentions remain distinct.',
      'Identity decisions carry lineage and can be revisited. The goal is not a magically clean graph—it is an auditable graph that can admit uncertainty.',
    ],
    status: 'planned', docs: ['mining', 'decisions'], scene: 'evidence',
    options: [
      {label: 'What records the reason for a merge?', next: 'evidence'},
      {label: 'How are facts represented across time?', next: 'knowledge'},
      {label: 'Show me the underlying decisions.', next: 'contract'},
    ],
  },
  evidence: {
    eyebrow: 'Trust, but inspect', question: 'What stops a polished answer from becoming invented lore?',
    answer: [
      'Claims link many-to-many with first-class Evidence records. Evidence points back to the capture, source span, mining lineage, and lifecycle state that justify the claim.',
      'Surfacing should prefer grounded claims and make the source inspectable. The evidence model is accepted; the mining and query experience that uses it is still planned.',
    ],
    status: 'planned', docs: ['mining', 'lifecycle', 'decisions'], scene: 'evidence',
    options: [
      {label: 'What if the source is deleted?', next: 'deletion'},
      {label: 'How does this become a future answer?', next: 'recall'},
      {label: 'Where is all of this stored?', next: 'privacy'},
    ],
  },
  builder: {
    eyebrow: 'Build an observer', question: 'What is the smallest useful Loreholm integration?',
    answer: [
      'Build an adapter that notices source-specific context and hands it to the embedded spine. The spine applies policy, creates a canonical envelope, queues when necessary, authenticates, and delivers it.',
      'Do not embed mining logic or graph writes in the adapter. A thin observer stays portable; the instance remains the single place where knowledge policy evolves.',
    ],
    status: 'mixed', docs: ['clients', 'capture', 'development'], scene: 'builder',
    options: [
      {label: 'Where does adapter responsibility end?', next: 'adapter'},
      {label: 'Show me the capture contract.', next: 'contract'},
      {label: 'How do I run the stack?', next: 'operations'},
    ],
  },
  adapter: {
    eyebrow: 'A deliberately weak sensor', question: 'Why can’t my adapter write entities directly?',
    answer: [
      'An observer sees only one application’s slice of reality. Letting it assert graph truth would scatter identity, salience, policy, and migration logic across every integration.',
      'V2 keeps adapters factual: observe, normalize source details, and deliver. The instance mines across sources with one policy and one evidence model.',
    ],
    status: 'planned', docs: ['clients', 'architecture'], scene: 'builder',
    options: [
      {label: 'What does the embedded spine add?', next: 'offline'},
      {label: 'What must every envelope contain?', next: 'envelope'},
      {label: 'Who is allowed to write the graph?', next: 'mining'},
    ],
  },
  contract: {
    eyebrow: 'The stable seam', question: 'What contract should an integration depend on?',
    answer: [
      'Depend on the versioned capture envelope and receipt semantics—not ArcadeDB types. Stable IDs, source metadata, timestamps, capture class, policy context, and payload cross the seam.',
      'That lets storage and mining schemas evolve without forcing every client to understand the graph. Unknown classes are quarantined instead of silently discarded.',
    ],
    status: 'implemented', docs: ['capture', 'clients'], scene: 'builder',
    options: [
      {label: 'How do retries avoid duplicates?', next: 'offline'},
      {label: 'Where do unknown capture classes go?', next: 'operations'},
      {label: 'How does captured context become useful?', next: 'knowledge'},
    ],
  },
  operations: {
    eyebrow: 'Raise the world', question: 'What can I actually run today?',
    answer: [
      'The current foundation installs a containerized V2 instance, generates separated credentials, exposes the authenticated instance boundary through the tunnel shim, persists captures in ArcadeDB, and supports browser-chat capture.',
      'The miner, reusable client spine, retention coordinator, and evidence-backed recall experience remain clearly marked planned work.',
    ],
    status: 'implemented', docs: ['operations', 'development', 'architecture'], scene: 'builder',
    options: [
      {label: 'Show me the private boundary.', next: 'privacy'},
      {label: 'Try the browser-chat journey.', next: 'recall'},
      {label: 'What should be built next?', next: 'mining'},
    ],
  },
};

export const looseThreads = [
  'cloud', 'offline', 'identity', 'deletion', 'adapter', 'evidence', 'egress', 'operations',
];

export const fileToId = Object.fromEntries(documents.map((doc) => [doc.file.toLowerCase(), doc.id]));

export const getDocument = (id) => documents.find((doc) => doc.id === id);
export const getRegion = (id) => regions.find((region) => region.id === id);
export const getAdventureNode = (id) => adventureNodes[id];
export const getQuestionsForDocument = (docId) => Object.entries(adventureNodes)
  .filter(([, node]) => node.docs.includes(docId))
  .map(([id, node]) => ({id, question: node.question, status: node.status}));
