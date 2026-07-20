const flow = (title, items, takeaway) => ({type: 'flow', title, items, takeaway});
const compare = (title, left, right, takeaway) => ({type: 'compare', title, left, right, takeaway});
const boundary = (title, zones, takeaway) => ({type: 'boundary', title, zones, takeaway});
const timeline = (title, items, takeaway) => ({type: 'timeline', title, items, takeaway});
const network = (title, center, items, takeaway) => ({type: 'network', title, center, items, takeaway});
const stack = (title, items, takeaway) => ({type: 'stack', title, items, takeaway});
const funnel = (title, inputs, gate, outputs, takeaway) => ({type: 'funnel', title, inputs, gate, outputs, takeaway});

const item = (label, detail) => ({label, detail});
const side = (label, items) => ({label, items});

export const visualizations = {
  what: flow('From working context to grounded recall', [
    item('Original context', 'Conversations and connected sources stay intact'),
    item('Selective mining', 'Useful moments become structured candidates'),
    item('Knowledge graph', 'People, decisions, time, and evidence connect'),
    item('Future answer', 'Recall returns meaning with a path to proof'),
  ], 'RAG is the starting point; a provenance-rich knowledge layer is the upgrade.'),

  morals: boundary('Who should hold a working history?', [
    item('Your instance', 'Durable context, policy, evidence, and memory'),
    item('Permission gate', 'Only an allowed representation may leave'),
    item('Model provider', 'Replaceable compute—not the memory owner'),
  ], 'Intelligence may be rented. The durable record remains yours.'),

  sharingamount: funnel('The prompt is only one ingredient', [
    'Your message', 'Earlier turns', 'System rules', 'Retrieved memory', 'Files & tools', 'App metadata',
  ], 'Effective request', ['Chosen model'], 'What reaches an LLM can be much larger than what is visible in the text box.'),

  visibility: flow('An inspectable model request', [
    item('Sources', 'User text, history, files, tools'),
    item('Transformations', 'Selection, redaction, summarization'),
    item('Decision', 'Policy, destination, and token size'),
    item('Local record', 'An auditable account of what crossed'),
  ], 'The target experience is a manifest before egress and a local receipt afterward.'),

  providerboundary: boundary('A gate before the model provider', [
    item('User-owned layer', 'Capture, storage, policy, mining, and durable memory'),
    item('Bifrost seam', 'Choose and constrain the model call'),
    item('Remote provider', 'Receives only the permitted request'),
  ], 'Loreholm changes custody and leverage—not the provider’s boundary after delivery.'),

  remoteprivacy: compare('Self-hosted storage is not remote invisibility',
    side('Local model', ['Context stays on your infrastructure', 'Strongest technical privacy boundary', 'You operate the compute']),
    side('Remote model', ['Permitted payload leaves your instance', 'Provider settings and contracts matter', 'Local custody still survives']),
    'Self-hosting controls Loreholm’s copy; it cannot erase a payload already sent elsewhere.'),

  providerretention: boundary('Where technical control ends', [
    item('Before egress', 'Block, minimize, route, and record'),
    item('Delivery line', 'The chosen payload crosses the boundary'),
    item('Provider systems', 'Retention is governed by their controls and contract'),
  ], 'If retention is unacceptable, keep processing local or choose a sufficient provider agreement.'),

  captureethics: compare('A private recorder can still overreach',
    side('Bounded capture', ['Intentionally connected sources', 'Visible classes and local policy', 'Inspection, deletion, and revocation']),
    side('Surveillance', ['Hidden or compulsory collection', 'Everything by default', 'No practical way to challenge it']),
    'Ownership is necessary, but consent and restraint still define ethical capture.'),

  upgrade: flow('The database is built, not merely filled', [
    item('Immutable capture', 'Identity, time, source, policy, payload'),
    item('Mining stages', 'Episodes, mentions, claims, provenance'),
    item('Resolution', 'Deduplicate identities and validate schema'),
    item('Grounded graph', 'Claims connect to independent evidence'),
  ], 'Vectors help find candidates; they are not treated as truth.'),

  chunks: compare('Similarity versus understanding',
    side('Chunk-only RAG', ['Passages split from their larger story', 'Similar text can flood retrieval', 'Time and identity remain ambiguous']),
    side('Loreholm', ['Raw source remains inspectable', 'Selected records carry context', 'Identity, time, and evidence become explicit']),
    'Embeddings become one retrieval instrument rather than the database’s worldview.'),

  salience: funnel('Useful signal before expensive work', [
    'User-authored volume', 'Turn count', 'Source surface', 'Remember-this push', 'Tool noise', 'Retry loops',
  ], 'Trim + salience gate', ['Admit to mining', 'Mark skipped'], 'Skipped material remains recoverable; the gate avoids spending model work on obvious noise.'),

  dedup: stack('Duplicate control at three layers', [
    item('Knowledge', 'Repeated observations strengthen one claim'),
    item('Mining', 'Matching fingerprints reuse successful work'),
    item('Transport', 'Stable capture IDs make retries idempotent'),
  ], 'Independent evidence adds confidence; replayed work adds nothing.'),

  retries: timeline('One capture, however many attempts', [
    item('Create ID', 'The client mints one stable UUIDv7'),
    item('Send', 'A timeout leaves the result uncertain'),
    item('Retry same ID', 'The original envelope is sent again'),
    item('Duplicate receipt', 'The unique index still holds one record'),
  ], 'A retry reuses the original ID—it never invents a new event.'),

  repeatedevidence: network('Many witnesses, one claim', item('Stable claim', 'Maya approved change X'), [
    item('Capture A', 'Independent conversation'),
    item('Evidence A', 'Source span and lineage'),
    item('Capture B', 'Independent meeting note'),
    item('Evidence B', 'Separate supporting path'),
  ], 'Two independent sources attach two evidence records to one claim.'),

  graphagent: funnel('Why one specialist commits the graph', [
    'Mined candidates', 'Existing identities', 'Relation rules', 'Time', 'Evidence', 'Current graph',
  ], 'Graph specialist + validator', ['Idempotent graph commit'], 'A narrow adapter cannot see enough context to decide shared graph truth.'),

  audience: compare('Two expeditions, one ownership model',
    side('One traveler', ['Recover personal decisions and reasons', 'Run a private instance', 'Accept an early technical product']),
    side('An organization', ['Pilot bounded non-critical sources', 'Own operations and policy', 'Review license and security duties']),
    'The best fit today is an operator who values custody more than turnkey polish.'),

  individual: timeline('A memory for future-you', [
    item('Today', 'A decision emerges in ordinary work'),
    item('Quiet capture', 'The original context stays on your instance'),
    item('Weeks later', 'You ask why the decision was made'),
    item('Grounded recall', 'The reason opens back to its source'),
  ], 'The aim is useful context without a separate filing ritual.'),

  technicalfit: timeline('The product path from operator to everyday user', [
    item('Now', 'Install, configure, and operate containers'),
    item('Today’s fit', 'Technical early adopters and bounded pilots'),
    item('Next', 'Reusable clients and knowledge mining'),
    item('Destination', 'A product that does not require infrastructure fluency'),
  ], 'You need technical comfort today; that is a project stage, not the end-state.'),

  organization: boundary('Memory inside organizational walls', [
    item('Organization-controlled instance', 'Capture, storage, policy, credentials, evidence'),
    item('Explicit model route', 'Local or contractually approved remote processing'),
    item('External compute', 'Replaceable and limited to permitted payloads'),
  ], 'A bounded pilot can test custody and operations before the knowledge layer is complete.'),

  pilot: flow('A sensible first pilot', [
    item('Choose a narrow source', 'Non-critical, consented, and easy to inspect'),
    item('Deploy privately', 'Organization-owned instance and model route'),
    item('Measure the foundation', 'Capture reliability and operator burden'),
    item('Review the gap', 'Do not score planned recall as if it exists'),
  ], 'Pilot the present foundation honestly; preserve room for the future payoff.'),

  licensing: stack('License boundaries inside an organization', [
    item('Your private modifications', 'Internal use stays internal under AGPL'),
    item('Network service obligations', 'Modified AGPL service source goes to its users'),
    item('Client on-ramps', 'Browser-facing clients carry the MIT carve-out'),
  ], 'Deployment model and distribution determine the practical obligations.'),

  remember: network('A decision is more than a sentence', item('Decision', 'Choose the local model'), [
    item('Reason', 'Privacy outweighed a small accuracy gain'),
    item('People', 'Who participated or approved'),
    item('Time', 'When it became true or changed'),
    item('Evidence', 'The original conversation and source span'),
  ], 'Loreholm aims to recover the useful neighborhood around a fact.'),

  examples: network('Questions a connected memory could answer', item('Your memory', 'Meaning across moments'), [
    item('Why?', 'Why did we choose this approach?'),
    item('Who?', 'Who approved the change?'),
    item('When?', 'When did our understanding change?'),
    item('Proof?', 'Which source supports the answer?'),
  ], 'The value comes from relationships and evidence, not just matching words.'),

  habit: flow('Memory without homework', [
    item('Keep working', 'Use a tool you deliberately connected'),
    item('Observe quietly', 'The integration notices permitted context'),
    item('Stage locally', 'Original context is saved on your instance'),
    item('Interpret later', 'Useful moments may become connected knowledge'),
  ], 'Passive means no constant save ritual—not hidden or unlimited recording.'),

  recording: funnel('Attention has boundaries', [
    'Connected sources', 'Allowed capture classes', 'Local permission', 'Redaction rules',
  ], 'Instance policy', ['Capture narrowly', 'Reject or exclude'], 'Loreholm is designed to observe chosen surfaces, not indiscriminately record a life.'),

  private: boundary('Your hearth holds the durable copy', [
    item('Your infrastructure', 'Raw context, database, graph, policy, and evidence'),
    item('Controlled egress', 'A model sees only what policy permits'),
    item('Replaceable provider', 'Supplies compute without owning Loreholm memory'),
  ], 'The user-controlled instance is the system of record.'),

  selfhost: compare('Two self-hosting scopes',
    side('Private instance', ['Your data and local services', 'Connect through Loreholm’s public front door', 'Simpler operational boundary']),
    side('Whole server plane', ['Public API, identity integration, and private networking', 'You operate every control-plane service', 'Much larger security responsibility']),
    'Most self-hosters need the private instance—not the entire public service.'),

  serverplane: stack('Running the whole Loreholm server', [
    item('Public edge', 'TLS, routing, identity, abuse controls'),
    item('Private network control', 'Headscale, node enrollment, ACLs'),
    item('Application plane', 'API, relays, user-to-instance mapping'),
    item('Operations', 'Secrets, upgrades, monitoring, recovery'),
  ], 'Full-server self-hosting means becoming the service operator, not just the data owner.'),

  ownership: boundary('What remains under your control', [
    item('Instance', 'Raw captures, graph, policy, credentials, configuration'),
    item('Model gate', 'Destination and permitted representation'),
    item('Outside boundary', 'A remote provider’s systems after delivery'),
  ], 'Custody is broad but honest: remote egress creates a second trust boundary.'),

  access: flow('A private road back to your instance', [
    item('Your browser', 'Sign in from wherever you are'),
    item('Public front door', 'Authenticate and authorize the request'),
    item('Encrypted Tailnet', 'Relay only allowed application traffic'),
    item('Private instance', 'Answer without exposing local services publicly'),
  ], 'Remote access reaches the application boundary, not your database or host network.'),

  sharing: boundary('Selective sharing without opening the vault', [
    item('Private memory', 'The complete graph and raw evidence stay home'),
    item('Disclosure builder', 'Choose claims, evidence, and allowed detail'),
    item('Shared package', 'A bounded export with an explicit audience'),
  ], 'Selective disclosure is an accepted direction; the controls are not implemented yet.'),

  different: compare('Finding words versus recovering meaning',
    side('Search', ['Matches phrases, files, and nearby text', 'Excellent when you remember the wording', 'Returns documents or passages']),
    side('Loreholm', ['Connects people, reasons, time, and evidence', 'Useful when wording is forgotten', 'Returns a grounded understanding']),
    'Loreholm adds connected recall; it does not try to replace ordinary search.'),

  search: compare('Choose the simplest useful tool',
    side('Use search when…', ['You remember a phrase', 'You know the filename', 'One document likely contains the answer']),
    side('Use Loreholm when…', ['The answer spans several moments', 'Identity or changing truth matters', 'You need reasons and evidence']),
    'Keywords are faster for known text; connected memory helps with unknown relationships.'),

  time: timeline('Truth can change without erasing history', [
    item('January', '“The launch is in March” is supported'),
    item('February', 'A decision moves the launch to May'),
    item('Supersession', 'The newer claim replaces—not deletes—the old one'),
    item('Future query', 'Ask what is true now or what used to be true'),
  ], 'Temporal claims preserve both the current answer and the path by which it changed.'),

  mistakes: flow('A memory that can be challenged', [
    item('Surface a claim', 'Show the answer and attached evidence'),
    item('Inspect the source', 'Open the exact supporting context'),
    item('Challenge it', 'Correct, separate, or discard the interpretation'),
    item('Preserve lineage', 'Record what changed and why'),
  ], 'The aim is an auditable memory, not an unquestionable oracle.'),

  today: stack('The project today', [
    item('Planned', 'Entity resolution, graph-backed recall, lifecycle, reusable spine'),
    item('Partly available', 'Policy surface, structured extraction, model boundary'),
    item('Works today', 'Private instance, capture, tunnel, chat, operations'),
  ], 'The foundation is executable; the deeper knowledge experience remains under construction.'),

  promise: stack('Three labels, three honest claims', [
    item('Designed, not built yet', 'Accepted destination; not executable'),
    item('Partly available', 'Some layers work; named gaps remain'),
    item('Works today', 'Grounded in current code and operations'),
  ], 'Status belongs to every answer so roadmap language cannot masquerade as product behavior.'),

  models: boundary('One policy gate, two model paths', [
    item('Instance policy', 'Choose by capture class and model role'),
    item('Bifrost', 'The only permitted model-routing seam'),
    item('Local or remote model', 'Keep work local or send an allowed representation'),
  ], 'Loreholm does not require a remote model for every job; enforcement maturity varies by path.'),

  backup: stack('A complete recovery set', [
    item('Configuration & secrets', 'Instance identity, policy, and service settings'),
    item('Durable queues', 'Captures and admitted work awaiting processing'),
    item('Databases', 'Raw context, future graph, evidence, and lineage'),
    item('Coordinated restore', 'Bring versions and dependent state back together'),
  ], 'Copying a live database directory alone is not a supported recovery plan.'),

  technical: flow('The system, one responsibility at a time', [
    item('Connected tool', 'Observe source-specific context'),
    item('Embedded spine', 'Apply policy, package, queue, and deliver'),
    item('Private instance', 'Authenticate and durably stage'),
    item('Knowledge builder', 'Interpret and exclusively commit graph truth'),
  ], 'Separation keeps integrations weak and knowledge policy centralized.'),

  passive: flow('Passive capture without instant belief', [
    item('Connected tool', 'A useful moment occurs'),
    item('Shared client layer', 'Permission and source context become an envelope'),
    item('Private instance', 'The original context is saved first'),
    item('Knowledge stage', 'Later decide what deserves interpretation'),
  ], 'Saving an observation is not the same as declaring it true.'),

  envelope: stack('The capture envelope crossing the seam', [
    item('Payload', 'Event stream or object snapshot'),
    item('Policy context', 'Capture class and applicable decision'),
    item('Provenance', 'Source, device, adapter, two timestamps'),
    item('Identity', 'Contract version and stable capture ID'),
  ], 'A durable receipt confirms staging—not interpretation or endorsement.'),

  offline: timeline('The road closes; the moment waits', [
    item('Connection lost', 'Work continues in the connected tool'),
    item('Local queue', 'Permitted context waits with its original ID'),
    item('Reconnect', 'The same envelope resumes delivery'),
    item('Idempotent receipt', 'Repeated delivery still creates one capture'),
  ], 'Server-side deduplication exists today; the reusable client queue is planned.'),

  recall: timeline('One conversation, three weeks of distance', [
    item('Today', '“Keep this local; privacy matters more”'),
    item('Saved context', 'The original conversation remains inspectable'),
    item('Knowledge', 'Decision, reason, and time become connected'),
    item('Three weeks later', '“Why did we choose that?” opens the source'),
  ], 'Capture works today; the deeper mined answer remains planned.'),

  privacy: boundary('Public access without public memory', [
    item('Public front door', 'Authenticates identity and relays allowed traffic'),
    item('Encrypted private route', 'Reaches only the instance application shim'),
    item('Your instance', 'Context, database, and local AI remain on your ground'),
  ], 'The database and host never become public internet services.'),

  cloud: compare('The public front door has a narrow job',
    side('Allowed', ['Authenticate the person', 'Map user to private instance', 'Relay approved application traffic']),
    side('Not its authority', ['Graph writes and raw retention', 'Capture and model policy', 'Database or Bifrost administration']),
    'The public edge is a gate and relay—not the owner of private knowledge.'),

  credentials: network('A different key for every gate', item('Your instance', 'No universal application secret'), [
    item('Browser OIDC', 'Ends at the public front door'),
    item('Sync credential', 'Front door to one private instance'),
    item('Device capture key', 'Writes permitted envelopes only'),
    item('Admin secrets', 'Dashboard, database, and Bifrost stay separate'),
  ], 'Compromise of one role should not silently grant every other role.'),

  securitygaps: compare('Foundation versus unfinished guarantees',
    side('Implemented boundary', ['Authenticated capture and administration', 'Private tunnel and local service isolation', 'Separated application credentials']),
    side('Still incomplete', ['Client-side policy and revocation', 'Budgets, retention, and deletion', 'Coordinated backup and sharing']),
    'A containerized private foundation is not yet a complete production security program.'),

  egress: flow('Every model call crosses one policy seam', [
    item('Capture class', 'Which kind of context is involved?'),
    item('Instance policy', 'Local-only or explicitly remote-capable?'),
    item('Bifrost route', 'No direct-provider fallback'),
    item('Chosen model', 'Local endpoint or permitted remote endpoint'),
  ], 'Structured extraction enforces this path today; budgets and safe remote transformations remain unfinished.'),

  localonly: boundary('Two gates and no hidden cloud road', [
    item('Client-side gate', 'The future spine blocks disallowed upload'),
    item('Instance-side gate', 'The extractor rechecks every source before egress'),
    item('Bifrost only', 'Permitted calls have no direct-provider fallback'),
  ], 'Instance-side mining enforcement works; end-to-end enforcement still awaits the spine.'),

  minerrole: compare('Models advise; deterministic rules commit',
    side('Model-assisted', ['Extract mentions and candidate claims now', 'Ambiguous identity remains planned', 'Summaries and schema proposals remain planned']),
    side('Mechanical', ['Trim and salience admission', 'Fingerprint and idempotency checks', 'Schema validation and final commit']),
    'A model has a bounded assignment—not permission to bypass the pipeline.'),

  policysurface: stack('The policy clients can read', [
    item('Mining status', 'Paused by default or explicitly active'),
    item('Per-class decision', 'Capture allowed and remote-processing mode'),
    item('Known classes', 'Supported context categories'),
    item('Contract range + revision', 'Which envelope versions and policy generation apply'),
  ], 'The instance and extractor enforce today; future clients must enforce before upload too.'),

  deletion: flow('Deletion follows the evidence graph', [
    item('Delete source', 'Begin a recoverable grace period'),
    item('Hard delete', 'Remove capture and its evidence links'),
    item('Re-evaluate claims', 'Check for other independent support'),
    item('Keep or remove', 'Survive with changed provenance—or disappear'),
  ], 'Deleting one source need not erase a claim that still has independent evidence.'),

  knowledge: flow('From saved history to connected meaning', [
    item('History', 'Preserve what happened'),
    item('Selection', 'Choose context worth deeper work'),
    item('Connection', 'Resolve identity, relationships, and time'),
    item('Knowledge', 'Ground conclusions in inspectable sources'),
  ], 'Storage remembers sentences; the knowledge builder connects their meaning.'),

  mining: flow('The only road to graph truth', [
    item('Capture', 'Immutable original context'),
    item('Admission', 'Trim, salience, and idempotent stages'),
    item('Interpretation', 'Episodes, mentions, identities, claims'),
    item('Validated commit', 'Evidence-backed graph changes'),
  ], 'Clients observe and deliver; only the instance miner may commit knowledge.'),

  identity: flow('A name becomes a candidate—not a verdict', [
    item('Mention', '“Maya” inside its source context'),
    item('Candidate retrieval', 'Aliases and embeddings find possibilities'),
    item('Conservative decision', 'Merge, link, or keep separate'),
    item('Lineage', 'Record the decision so it can be revisited'),
  ], 'Matching text is evidence for identity, never identity by itself.'),

  evidence: network('A claim with inspectable roots', item('Claim', 'A stable subject–relation–object assertion'), [
    item('Evidence A', 'Capture, source span, and lineage'),
    item('Evidence B', 'Independent supporting source'),
    item('Lifecycle', 'Active, deleted, or superseded support'),
    item('Answer', 'Surface the claim and open its proof'),
  ], 'Claims and evidence are many-to-many so provenance can change without rewriting history.'),

  builder: flow('The smallest useful integration', [
    item('Thin adapter', 'Notice source-specific context'),
    item('Embedded spine', 'Apply policy and build the envelope'),
    item('Reliable delivery', 'Queue, authenticate, send, and receive'),
    item('Private instance', 'Store first; interpret centrally later'),
  ], 'Build an observer, not a second knowledge system.'),

  adapter: compare('Keep the sensor deliberately weak',
    side('Adapter may', ['Observe source-specific facts', 'Normalize local details', 'Hand context to the shared spine']),
    side('Adapter may not', ['Resolve global identity', 'Declare claims as truth', 'Write directly into the knowledge graph']),
    'Centralizing judgment keeps policy, evidence, and migrations consistent across tools.'),

  spine: stack('One shared discipline inside every tool', [
    item('Delivery', 'Batch authentication, receipts, retry, revocation'),
    item('Offline queue', 'Ordering, queue age, and original IDs'),
    item('Local policy', 'Permission, redaction, capture classes'),
    item('Canonical envelope', 'Device, adapter, timestamps, stable identity'),
  ], 'Adapters stay small because the spine owns common privacy and delivery behavior.'),

  contract: boundary('A stable seam over evolving internals', [
    item('Connected tool', 'Depends on envelope fields and receipt semantics'),
    item('Versioned capture boundary', 'IDs, source, time, class, policy, payload'),
    item('Evolving instance', 'Storage and mining schemas may change independently'),
  ], 'Integrations depend on the contract—not on ArcadeDB types.'),

  unknownclasses: flow('Unknown does not mean discarded', [
    item('Valid envelope', 'Authentication and contract checks pass'),
    item('Policy lookup', 'Capture class is not recognized'),
    item('Durable quarantine', 'Store the complete envelope in isolation'),
    item('No interpretation', 'Block mining, sharing, and model egress'),
  ], 'A quarantined receipt preserves the observation until explicit support exists.'),

  operations: compare('What the foundation runs today',
    side('Operational now', ['Containerized private instance', 'Separated credentials and tunnel shim', 'Capture, extraction, resolution, Claim/Evidence commit']),
    side('Still planned', ['Grounded query and surfacing', 'Reusable embedded spine', 'Retention and coordinated recovery', 'Correction experience']),
    'The current system is a working capture foundation, not yet the complete memory experience.'),
};

export const getVisualization = (id) => visualizations[id];
