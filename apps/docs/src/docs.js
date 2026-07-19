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
import development from '../../../docs/10_Development.md?raw';

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
    summary: 'How passive capture, the private network, and the planned knowledge-building pipeline fit together.',
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
    short: 'Follow the first complete Loreholm expedition.', region: 'threshold', status: 'implemented', depth: 1,
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
    id: 'development', file: '10_Development.md', numeral: 'XI', title: 'Development Stack',
    short: 'Enter the workshop with the right instruments.', region: 'workshop', status: 'implemented', depth: 3,
    time: '6 min', color: '#b8b5ff', raw: development,
    summary: 'Local Compose development, vLLM GPU overlay, model constraints, and browser-chat networking.',
  },
];

export const entryQuestions = [
  {name: 'Rune of Memory', label: 'Could Loreholm help me remember why we made a decision?', next: 'remember', mark: 'ᛗ'},
  {name: 'Rune of the Hearth', label: 'Can I use it without giving away my private work?', next: 'private', mark: 'ᛉ'},
  {name: 'Rune of Insight', label: 'What would it know that ordinary search does not?', next: 'different', mark: 'ᚨ'},
  {name: 'Rune of First Light', label: 'What can I actually do with Loreholm today?', next: 'today', mark: 'ᛏ'},
];

export const adventureNodes = {
  remember: {
    depth: 1, eyebrow: 'A memory that keeps its reasons', question: 'What would Loreholm remember for me?',
    answer: [
      'Loreholm is meant to remember the story around your work: what was decided, who or what it involved, when it changed, and where that understanding came from.',
      'The goal is simple: weeks later, you should be able to ask a natural question and recover more than a matching sentence. You should recover the useful context around it.',
    ],
    status: 'mixed', docs: ['architecture'], scene: 'recall',
    options: [
      {label: 'Show me a few everyday examples.', next: 'examples'},
      {label: 'Would I have to stop and write everything down?', next: 'habit'},
      {label: 'How much of that works today?', next: 'today'},
    ],
  },
  examples: {
    depth: 1, eyebrow: 'What future-you could ask', question: 'What kinds of questions could Loreholm eventually answer?',
    answer: [
      '“Why did we choose this approach?” “When did the plan change?” “Who knows about this project?” “What did I promise to follow up on?” “Where did this fact come from?”',
      'Those answers can draw on conversations and other connected sources, while keeping a path back to the original moment instead of presenting a mysterious, context-free answer.',
    ],
    status: 'mixed', docs: ['architecture', 'chat'], scene: 'recall',
    options: [
      {label: 'Walk me through one remembered decision.', next: 'recall'},
      {label: 'Can it understand that facts change over time?', next: 'time'},
      {label: 'How is that different from searching my history?', next: 'different'},
    ],
  },
  habit: {
    depth: 2, eyebrow: 'Memory without homework', question: 'Would I need to stop working and tell Loreholm what to save?',
    answer: [
      'That is exactly what Loreholm is trying to avoid. Connected tools can notice useful context as you work and send it to your own Loreholm instance in the background.',
      'You should still control which tools participate and what kinds of context they may share. Passive should mean low-friction—not invisible or uncontrollable.',
    ],
    status: 'mixed', docs: ['architecture', 'clients'], scene: 'recall',
    options: [
      {label: 'How does background remembering work?', next: 'passive'},
      {label: 'Does that mean it records absolutely everything?', next: 'recording'},
      {label: 'What happens if I am offline?', next: 'offline'},
    ],
  },
  recording: {
    depth: 2, eyebrow: 'Boundaries around attention', question: 'Is Loreholm supposed to record everything I do?',
    answer: [
      'No. Loreholm receives context from tools you intentionally connect. Each connection has a defined kind of information it can observe, and instance-owned policy is meant to decide what may be kept or processed.',
      'The current foundation proves the capture path. The richer controls that make those boundaries easy to inspect and change are still part of the work ahead.',
    ],
    status: 'mixed', docs: ['policy', 'clients', 'capture'], scene: 'privacy',
    options: [
      {label: 'Where does the remembered context live?', next: 'ownership'},
      {label: 'What happens after a tool notices something?', next: 'passive'},
      {label: 'Show me the exact information it sends.', next: 'envelope'},
    ],
  },
  private: {
    depth: 1, eyebrow: 'A hearth of your own', question: 'Who owns and holds my Loreholm memory?',
    answer: [
      'Your lasting context is designed to live in your own Loreholm instance, on infrastructure you control. The public Loreholm service helps you reach it; it is not meant to become the permanent home of your private history.',
      'Think of the cloud service as a guarded harbor and your instance as the longhouse where the records actually stay.',
    ],
    status: 'implemented', docs: ['networking', 'security'], scene: 'privacy',
    options: [
      {label: 'What does “my own instance” really mean?', next: 'ownership'},
      {label: 'Can I reach it when I am away from home?', next: 'access'},
      {label: 'Could I choose to share something later?', next: 'sharing'},
    ],
  },
  ownership: {
    depth: 2, eyebrow: 'Your data, your ground', question: 'What stays under my control?',
    answer: [
      'The saved source material, the knowledge built from it, and the models that can process it locally belong inside your instance. The database and model service are not opened directly to the internet.',
      'Ownership also means responsibility: backups, device security, and deletion need clear tools. Some of those lifecycle tools are designed but not yet built.',
    ],
    status: 'mixed', docs: ['security', 'lifecycle', 'operations'], scene: 'privacy',
    options: [
      {label: 'How is it protected from the public internet?', next: 'privacy'},
      {label: 'What would a proper backup include?', next: 'backup'},
      {label: 'Can I make it forget something?', next: 'deletion'},
    ],
  },
  access: {
    depth: 2, eyebrow: 'A road back to the hearth', question: 'How can I use my private instance when I am somewhere else?',
    answer: [
      'You sign in through Loreholm’s public front door. It carries an allowed request through a private encrypted route to your instance, then returns the response.',
      'That route gives you access to the application without placing the database, local AI service, or the rest of your computer on the public internet.',
    ],
    status: 'implemented', docs: ['networking', 'chat'], scene: 'privacy',
    options: [
      {label: 'Show me how that private route is built.', next: 'privacy'},
      {label: 'What can the public service see?', next: 'cloud'},
      {label: 'What happens when that route is unavailable?', next: 'offline'},
    ],
  },
  sharing: {
    depth: 3, eyebrow: 'Carrying lore beyond the hearth', question: 'Could I share selected knowledge without sharing everything?',
    answer: [
      'That is the intended direction: sharing should be an explicit disclosure from your instance, not a side effect of using the product. Private source material should remain private unless you deliberately release something.',
      'The concrete sharing experience is not implemented yet. The design treats disclosure as important and potentially irreversible, so it cannot be a casual default.',
    ],
    status: 'planned', docs: ['lifecycle', 'security', 'architecture'], scene: 'privacy',
    options: [
      {label: 'What never needs to leave my instance?', next: 'privacy'},
      {label: 'Could remote AI still see some content?', next: 'egress'},
      {label: 'What if I later delete the source?', next: 'deletion'},
    ],
  },
  different: {
    depth: 1, eyebrow: 'More than finding words', question: 'How is Loreholm different from searching my notes or chat history?',
    answer: [
      'Search is excellent at finding matching words. Loreholm aims to remember meaning across sources: that two names refer to the same person, that a decision replaced an older one, or that an answer rests on several pieces of evidence.',
      'It is the difference between finding old pages and asking a careful librarian what the pages collectively say—and being able to inspect the pages afterward.',
    ],
    status: 'planned', docs: ['mining', 'architecture'], scene: 'evidence',
    options: [
      {label: 'When would search still be enough?', next: 'search'},
      {label: 'How can it understand change over time?', next: 'time'},
      {label: 'What if the librarian gets something wrong?', next: 'mistakes'},
    ],
  },
  search: {
    depth: 2, eyebrow: 'Finding versus understanding', question: 'When is ordinary search enough—and when is Loreholm useful?',
    answer: [
      'If you remember a phrase or filename, ordinary search may be the fastest tool. Loreholm becomes useful when your question depends on connections: reasons, people, changing facts, or evidence spread across several moments.',
      'Loreholm should not replace search. It should add a deeper kind of recall for questions that keywords alone cannot answer well.',
    ],
    status: 'planned', docs: ['mining'], scene: 'evidence',
    options: [
      {label: 'How is meaning built from raw history?', next: 'knowledge'},
      {label: 'Show me a decision recalled later.', next: 'recall'},
      {label: 'How would an answer show its sources?', next: 'evidence'},
    ],
  },
  time: {
    depth: 2, eyebrow: 'Truth that can change', question: 'Can Loreholm remember that something used to be true?',
    answer: [
      'That is part of the design. “Maya leads the project” and “Maya led the project last year” should not overwrite each other or become a contradiction. Each understanding can carry the period when it applied.',
      'This time-aware knowledge model is designed, but the system that builds and queries it is not yet implemented.',
    ],
    status: 'planned', docs: ['mining', 'architecture'], scene: 'evidence',
    options: [
      {label: 'How does raw history become that kind of knowledge?', next: 'knowledge'},
      {label: 'How does it know which Maya I mean?', next: 'identity'},
      {label: 'What proves when the change happened?', next: 'evidence'},
    ],
  },
  mistakes: {
    depth: 2, eyebrow: 'A memory that can be challenged', question: 'What happens when Loreholm misunderstands something?',
    answer: [
      'It should show where an understanding came from, keep uncertainty instead of hiding it, and preserve enough history to revisit an identity match or interpretation.',
      'The aim is not an all-knowing oracle. It is a memory system whose answers can be inspected, corrected, or discarded. The evidence model for that is designed; the full correction experience is not built yet.',
    ],
    status: 'planned', docs: ['mining', 'lifecycle'], scene: 'evidence',
    options: [
      {label: 'How are sources attached to an answer?', next: 'evidence'},
      {label: 'How does it avoid mixing up people?', next: 'identity'},
      {label: 'Can I delete the misunderstood source?', next: 'deletion'},
    ],
  },
  today: {
    depth: 1, eyebrow: 'What is real now', question: 'What part of Loreholm works today?',
    answer: [
      'The foundation can run a private instance, accept and safely store context, protect that instance behind the private network, and capture browser-chat conversations.',
      'The deeper promise—turning that history into connected, evidence-backed knowledge you can question—is the next major territory. The site marks that distinction throughout.',
    ],
    status: 'implemented', docs: ['operations', 'architecture', 'chat'], scene: 'builder',
    options: [
      {label: 'Show me the part I can use as an example.', next: 'recall'},
      {label: 'How is the private instance operated?', next: 'operations'},
      {label: 'How do I tell built features from future plans?', next: 'promise'},
    ],
  },
  promise: {
    depth: 2, eyebrow: 'Reading the map honestly', question: 'How does this site distinguish reality from roadmap?',
    answer: [
      'Every answer carries a plain status: “Works today,” “Partly available,” or “Designed, not built yet.” A planned feature is documented so the destination is clear, but it is never presented as something you can already run.',
      'You can stay at this plain-language level or keep descending into the source guides and architecture decisions behind each claim.',
    ],
    status: 'implemented', docs: ['architecture', 'security'], scene: 'builder',
    options: [
      {label: 'What is running in the current foundation?', next: 'operations'},
      {label: 'What is the largest unbuilt piece?', next: 'mining'},
      {label: 'Take me toward the technical details.', next: 'technical'},
    ],
  },
  models: {
    depth: 3, eyebrow: 'The intelligence behind the work', question: 'Does Loreholm require sending everything to a large online AI model?',
    answer: [
      'No. The design allows local models, remote models you explicitly permit, or a mixture based on the kind of task and your policy. Some jobs may need more capable models; others can remain entirely local.',
      'The routing foundation exists. The complete controls, budgets, and mining workflow that use it are still partly planned.',
    ],
    status: 'mixed', docs: ['policy', 'development'], scene: 'privacy',
    options: [
      {label: 'How is permission to use remote AI expressed?', next: 'egress'},
      {label: 'What would AI do during knowledge building?', next: 'mining'},
      {label: 'What can I run locally today?', next: 'operations'},
    ],
  },
  backup: {
    depth: 3, eyebrow: 'Keeping the hearth safe', question: 'What would I need to back up?',
    answer: [
      'A useful backup must preserve the saved context, the knowledge built from it, large attached content, configuration, and the information needed to restore them consistently.',
      'Copying a live database folder is not yet presented as a supported backup method. A coordinated backup and restore experience remains planned work.',
    ],
    status: 'planned', docs: ['lifecycle', 'operations'], scene: 'privacy',
    options: [
      {label: 'Why is this part of data ownership?', next: 'ownership'},
      {label: 'How is deletion different from backup?', next: 'deletion'},
      {label: 'What operational tools exist now?', next: 'operations'},
    ],
  },
  technical: {
    depth: 3, eyebrow: 'The path beneath the floorboards', question: 'I understand the promise. How is the system divided up?',
    answer: [
      'Connected tools observe context. A shared client layer packages and delivers it. Your instance authenticates and stores it. A future knowledge-building service interprets it and becomes the only component allowed to change the knowledge graph.',
      'That separation keeps individual integrations simple and keeps privacy, identity, evidence, and knowledge rules in one place.',
    ],
    status: 'mixed', docs: ['architecture', 'clients'], scene: 'builder',
    options: [
      {label: 'How would I connect a new tool?', next: 'builder'},
      {label: 'What is the exact delivery contract?', next: 'contract'},
      {label: 'How does knowledge building work internally?', next: 'mining'},
    ],
  },
  passive: {
    depth: 3, eyebrow: 'Passive memory', question: 'How does Loreholm remember without an explicit “save this” command?',
    answer: [
      'An integration observes useful context from a tool you connected. A shared client layer turns that context into a consistent package and delivers it to your private instance.',
      'The instance first saves the original context. It does not immediately treat every sentence as truth. A later knowledge-building step decides what deserves deeper interpretation.',
    ],
    status: 'mixed', docs: ['architecture', 'capture', 'chat'], scene: 'recall',
    options: [
      {label: 'What is inside one captured moment?', next: 'envelope'},
      {label: 'What if I work while disconnected?', next: 'offline'},
      {label: 'Show me how this helps future me.', next: 'recall'},
    ],
  },
  envelope: {
    depth: 5, eyebrow: 'The raw material', question: 'What exactly crosses the capture boundary?',
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
    depth: 3, eyebrow: 'Interrupted passage', question: 'Do I lose context when the private connection is unavailable?',
    answer: [
      'The planned client layer keeps unsent moments in a local queue. When the connection returns, it sends the same moments again without creating duplicate memories.',
      'The server can already recognize repeated deliveries. The reusable offline queue inside connected tools is still planned.',
    ],
    status: 'mixed', docs: ['clients', 'capture'], scene: 'offline',
    options: [
      {label: 'How does the private tunnel work?', next: 'privacy'},
      {label: 'How would I build an observer?', next: 'builder'},
      {label: 'What is implemented right now?', next: 'operations'},
    ],
  },
  recall: {
    depth: 2, eyebrow: 'The future payoff', question: 'How could one conversation help me three weeks later?',
    answer: [
      'Imagine saying, “Keep this part local; privacy matters more than the small accuracy gain.” Loreholm keeps the conversation now. Later, it could recognize the decision, its reason, and when it happened.',
      'When future-you asks why the choice was made, Loreholm could answer and open the exact conversation behind that answer. Saving the conversation works today; the deeper answer is still planned.',
    ],
    status: 'mixed', docs: ['chat', 'mining', 'architecture'], scene: 'recall',
    options: [
      {label: 'How can I trust the answer?', next: 'evidence'},
      {label: 'How does it recognize the same person twice?', next: 'identity'},
      {label: 'Can I remove the original conversation?', next: 'deletion'},
    ],
  },
  privacy: {
    depth: 3, eyebrow: 'The private boundary', question: 'How does Loreholm keep my saved context out of its public service?',
    answer: [
      'The public front door confirms your identity and relays allowed requests through an encrypted private network. The saved context, database, and local AI service remain inside your instance.',
      'The private route reaches only the Loreholm application boundary. It does not place your database, containers, or computer directly on the public internet.',
    ],
    status: 'implemented', docs: ['networking', 'security'], scene: 'privacy',
    options: [
      {label: 'What can the front door actually see?', next: 'cloud'},
      {label: 'Can model processing leave my instance?', next: 'egress'},
      {label: 'What happens when I delete something?', next: 'deletion'},
    ],
  },
  cloud: {
    depth: 4, eyebrow: 'The public edge', question: 'What is Loreholm’s public front door technically allowed to do?',
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
    depth: 4, eyebrow: 'Model egress', question: 'Under what policy could saved context reach a remote AI model?',
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
    depth: 4, eyebrow: 'Forgetting', question: 'If I delete a source, what happens to knowledge learned from it?',
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
    depth: 3, eyebrow: 'From history to meaning', question: 'How does saved history become connected knowledge?',
    answer: [
      'Saved history preserves what happened. It does not automatically know that two names mean the same person, that a statement replaced an older one, or which source supports a conclusion.',
      'The planned knowledge builder selects useful context, connects people and ideas, records change over time, and keeps the trail back to the source.',
    ],
    status: 'planned', docs: ['mining', 'architecture'], scene: 'evidence',
    options: [
      {label: 'What are the mining stages?', next: 'mining'},
      {label: 'How does identity resolution work?', next: 'identity'},
      {label: 'What makes an answer trustworthy?', next: 'evidence'},
    ],
  },
  mining: {
    depth: 4, eyebrow: 'The quiet librarian', question: 'How does the knowledge-building pipeline commit graph knowledge?',
    answer: [
      'Only the instance miner is allowed to make that commitment. It interprets captures, checks salience, resolves identities, forms claims and episodes, attaches evidence, and commits an idempotent result.',
      'This pipeline is accepted Loreholm design and remains planned. Clients stay deliberately less powerful: they observe and deliver, but never decide graph truth.',
    ],
    status: 'planned', docs: ['mining', 'architecture'], scene: 'evidence',
    options: [
      {label: 'How are two mentions joined?', next: 'identity'},
      {label: 'How does evidence survive re-mining?', next: 'evidence'},
      {label: 'Which model is allowed to do this?', next: 'egress'},
    ],
  },
  identity: {
    depth: 4, eyebrow: 'Names are not people', question: 'How does Loreholm decide that two mentions refer to the same entity?',
    answer: [
      'It does not equate matching text with identity. The accepted design uses normalized aliases, context, candidate retrieval, conservative matching, and merge records so uncertain mentions remain distinct.',
      'Identity decisions carry lineage and can be revisited. The goal is not a magically clean graph—it is an auditable graph that can admit uncertainty.',
    ],
    status: 'planned', docs: ['mining', 'architecture'], scene: 'evidence',
    options: [
      {label: 'What records the reason for a merge?', next: 'evidence'},
      {label: 'How are facts represented across time?', next: 'knowledge'},
      {label: 'Show me the underlying decisions.', next: 'contract'},
    ],
  },
  evidence: {
    depth: 4, eyebrow: 'Trust, but inspect', question: 'How is a graph claim tied back to inspectable evidence?',
    answer: [
      'Claims link many-to-many with first-class Evidence records. Evidence points back to the capture, source span, mining lineage, and lifecycle state that justify the claim.',
      'Surfacing should prefer grounded claims and make the source inspectable. The evidence model is accepted; the mining and query experience that uses it is still planned.',
    ],
    status: 'planned', docs: ['mining', 'lifecycle', 'architecture'], scene: 'evidence',
    options: [
      {label: 'What if the source is deleted?', next: 'deletion'},
      {label: 'How does this become a future answer?', next: 'recall'},
      {label: 'Where is all of this stored?', next: 'privacy'},
    ],
  },
  builder: {
    depth: 3, eyebrow: 'Build an observer', question: 'What is the smallest useful way to connect a new tool?',
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
    depth: 4, eyebrow: 'A deliberately weak sensor', question: 'Why can’t an adapter write entities directly?',
    answer: [
      'An observer sees only one application’s slice of reality. Letting it assert graph truth would scatter identity, salience, policy, and migration logic across every integration.',
      'Loreholm keeps adapters factual: observe, normalize source details, and deliver. The instance mines across sources with one policy and one evidence model.',
    ],
    status: 'planned', docs: ['clients', 'architecture'], scene: 'builder',
    options: [
      {label: 'What does the embedded spine add?', next: 'offline'},
      {label: 'What must every envelope contain?', next: 'envelope'},
      {label: 'Who is allowed to write the graph?', next: 'mining'},
    ],
  },
  contract: {
    depth: 5, eyebrow: 'The stable seam', question: 'What exact contract should an integration depend on?',
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
    depth: 3, eyebrow: 'Raise the world', question: 'What services and operations exist in the current foundation?',
    answer: [
      'The current foundation installs a containerized Loreholm instance, generates separated credentials, exposes the authenticated instance boundary through the tunnel shim, persists captures in ArcadeDB, and supports browser-chat capture.',
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
  'examples', 'recording', 'access', 'search', 'today', 'models', 'sharing', 'mistakes',
];

export const fileToId = Object.fromEntries(documents.map((doc) => [doc.file.toLowerCase(), doc.id]));

export const getDocument = (id) => documents.find((doc) => doc.id === id);
export const getRegion = (id) => regions.find((region) => region.id === id);
export const getAdventureNode = (id) => adventureNodes[id];
export const getQuestionsForDocument = (docId) => Object.entries(adventureNodes)
  .filter(([, node]) => node.docs.includes(docId))
  .map(([id, node]) => ({id, question: node.question, status: node.status, depth: node.depth}))
  .sort((a, b) => a.depth - b.depth);
