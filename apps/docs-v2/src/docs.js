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

export const paths = [
  {
    id: 'first-fire', eyebrow: 'Recommended first journey', title: 'Light the first fire',
    description: 'Understand the promise, watch a conversation cross the boundary, then inspect what was captured.',
    docs: ['architecture', 'chat', 'capture'], color: '#d9f99d', duration: '24 min',
  },
  {
    id: 'gatekeeper', eyebrow: 'Operator path', title: 'Keep the outer wall',
    description: 'Trace every public hop, raise an instance, and audit the security boundary.',
    docs: ['networking', 'operations', 'security'], color: '#66d6cf', duration: '24 min',
  },
  {
    id: 'librarian', eyebrow: 'Deep systems path', title: 'Descend into the archive',
    description: 'Follow raw context until it becomes grounded, temporal, provenance-backed knowledge.',
    docs: ['policy', 'mining', 'lifecycle'], color: '#e87951', duration: '32 min',
  },
  {
    id: 'builder', eyebrow: 'Integrator path', title: 'Build a new observer',
    description: 'Learn the capture seam, the client boundary, and the local development terrain.',
    docs: ['capture', 'clients', 'development'], color: '#a9a6ff', duration: '23 min',
  },
];

export const fileToId = Object.fromEntries(documents.map((doc) => [doc.file.toLowerCase(), doc.id]));

export const getDocument = (id) => documents.find((doc) => doc.id === id);
export const getRegion = (id) => regions.find((region) => region.id === id);
