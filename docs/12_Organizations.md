# Using Loreholm in an organization

Loreholm can be evaluated and operated inside an organization. Its strongest
fit is a team that already uses LLMs for meaningful work, needs continuity
across conversations and tools, and is unwilling to place its durable working
memory in a third-party black box. The current release is suitable for a
bounded technical pilot, not yet for mission-critical organizational memory.

## What an organization gets

Loreholm separates four concerns that are often collapsed into one hosted AI
product:

- connected tools observe context without deciding what is true;
- each private instance owns raw context, policy, models, and future knowledge;
- the server plane handles identity and narrow remote access without becoming
  the durable memory store; and
- mining stages, rather than every client integration, will own deduplication,
  identity, evidence, and graph construction.

That separation lets an organization choose where data lives, which model
endpoints may see each class of content, and whether to operate only private
instances or the complete Loreholm server plane.

## A sensible first pilot

Start with a small group, a non-critical body of work, and one clearly bounded
source such as Loreholm browser chat. Decide in advance what useful result you
want to recover later: a decision and its rationale, a changing project fact,
or the evidence behind a recommendation. Keep the instance on organization-
controlled infrastructure and choose a model route that matches the data
classification. Review captured records, logs, access, deletion expectations,
and failure modes before connecting another source.

The current browser-chat path can test capture, private routing, local storage,
authentication, and model configuration. The evidence-backed mining and recall
experience is still planned, so a pilot today evaluates the foundation and the
trust boundary rather than a finished organizational knowledge product.

## Deployment choices

An organization can:

1. run private instances while using the standard Loreholm front door;
2. operate both the private instances and the public server plane; or
3. keep an instance local and connect to it only through organization-managed
   private networking.

The complete server-plane Compose bundle includes the browser app, API, Caddy,
Headscale, a Tailscale sidecar, and Redis. Each person's durable memory remains
in a separate private instance. See [self-hosting](11_SelfHosting.md) and
[Loreholm networking](02_Networking.md).

## Security responsibilities

Loreholm provides a narrow application route instead of exposing the database,
model gateway, Docker network, or host. An organization operating the system is
still responsible for host security, disk encryption, OIDC configuration, DNS,
TLS, secret management, model-provider policy, monitoring, and recovery.

The server plane can handle content while relaying a live request. Durable
captures and knowledge stay in the private instance, but a remote identity
provider or model endpoint remains an external trust boundary if the
organization chooses one. Read the [security model](13_SecurityModel.md) before
using sensitive data.

## Licensing

The server-side repository code is licensed under AGPL-3.0. Client-facing code
under `web/` and `apps/chat/` is MIT-licensed. Organizations may run and modify
the software, but should review the applicable licenses—especially AGPL source
obligations when modified server software is offered over a network—with their
own counsel. The software licenses govern code, not the organization's captured
data; that data remains the user's.

Dependencies, model weights, model providers, identity providers, and other
connected services retain their own terms.

## Adoption gates

Do not treat Loreholm as the sole copy of important organizational knowledge
yet. Before production adoption, an organization should require:

- supported, tested backup and restore;
- retention, deletion, and legal-hold behavior appropriate to its obligations;
- monitoring, incident response, and an upgrade runbook;
- review of the Headscale ACL, tunnel shim, credentials, and container boundary;
- validation of the models chosen for mining and surfacing; and
- a clear support plan—the upstream project currently has no SLA.

The project documents planned behavior early so teams can evaluate the design,
but every pilot should distinguish implemented foundation from planned mining,
graph, and lifecycle capabilities.
