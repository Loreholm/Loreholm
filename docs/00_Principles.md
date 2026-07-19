# Why Loreholm exists

**Status:** The user-owned capture and model-gateway foundation exists today.
Complete egress inspection, mining, retention, deletion, and grounded recall are
product requirements that remain partly or wholly unimplemented.

Loreholm begins with an uncomfortable observation: the message visible in an
LLM text box is often not the whole request. A conversation surface may also
send conversation history, system instructions, attached files, retrieved
memory, tool results, IDE context, page content, or other application-provided
material. The exact envelope varies by tool, provider, settings, and contract,
and users are not always given one place to inspect it.

That context can contain the working history of a person or organization. It
contains decisions, unfinished ideas, relationships, mistakes, source code,
business facts, and the reasons behind later actions. Loreholm's position is
that access to useful intelligence should not silently require surrendering
custody of that memory.

## The moral claim

The person or organization creating context should control the durable layer
that remembers it. They should be able to see what was captured, decide what a
model may receive, choose where processing happens, inspect what knowledge was
derived, and remove material through a real lifecycle. A model provider should
be a chosen compute endpoint, not the automatic home of the user's history.

Loreholm therefore steps between connected tools and model providers with a
user-owned layer for capture, policy, storage, mining, and evidence:

```text
without a user-owned layer

your work -> application-selected context -> model provider
                                             `- provider-defined handling

with Loreholm

your work -> inspectable capture -> your policy -> your storage and mining
                                      |
                                      `-> explicitly permitted representation
                                           -> local or remote model
```

This is not a claim that every provider misuses data. Provider retention,
training, abuse monitoring, human review, and contractual controls differ.
The point is that custody and observability should not depend entirely on
provider promises when the user can own the memory layer directly.

## Show the whole envelope

Loreholm should make a distinction between:

- what the person typed or deliberately attached;
- what the connected application added automatically;
- what prior history or retrieved knowledge was included;
- what transformations or redactions were applied;
- which model endpoint received the result; and
- what durable local record supports later inspection.

The long-term requirement is an inspectable egress record for every model call:
the role and endpoint, policy decision, source classes, representation level,
time, size, and exact locally inspectable payload or a deliberately documented
alternative when retaining that payload would create greater risk.

Loreholm does not provide that universal egress ledger today. Browser chat
captures the user's message and completed assistant response, Bifrost is the
single model-egress seam, and instance policy can express remote-processing
modes. Capturing application-added context across tools and showing the exact
effective request remain work to be implemented.

## Storage before provider memory

If a user wants continuity, the first durable copy should be in storage they
control. Loreholm keeps captured context in the user's instance and plans to
mine it there into derived vectors, entities, claims, relationships, and
evidence. The public front door routes authorized requests; it is not designed
to become the permanent memory store.

This makes the external model replaceable. A user can choose a local endpoint,
a remote endpoint allowed for a particular class of content, or different
models for different roles. Changing the model provider should not require
giving up the memory already built from the user's work.

## Capture can become surveillance too

A user-owned recorder is still a recorder. Self-hosting does not make unlimited
collection ethical, and “passive” must never mean hidden, compulsory, or
impossible to inspect.

Loreholm's design therefore requires:

- intentional connection of every source;
- visible capture classes and local policy;
- data minimization before expensive or remote processing;
- no client-side authority to declare graph truth;
- provenance for derived claims;
- revocation, retention, deletion, and export controls; and
- honest status labels wherever those controls are not implemented.

Several of these controls remain planned. Until capture inventory, universal
egress inspection, retention, and deletion are implemented, operators should
use bounded sources and non-critical data rather than treating self-hosting as
a complete consent system.

## What self-hosting does not promise

Self-hosting gives the user custody of Loreholm's durable context, policy,
database, graph, and model-routing configuration. It does not make a remote
model local. Once an explicitly permitted payload is sent to an external
provider, that provider's technical behavior, account settings, contract,
jurisdiction, and retention policy still matter.

The strongest way to keep content from a provider is not to send it: use a
local model or keep the relevant capture class in local-only mode. When a
remote model is useful, Loreholm's job is to make the choice visible, minimize
the representation, enforce the user's policy, and preserve enough local
evidence to audit what happened.

## Principles that should survive implementation

1. **User custody first.** Durable working memory belongs in the user's
   instance unless the user explicitly chooses otherwise.
2. **The full request should be visible.** A person should be able to inspect
   application-added context, not only the text they typed.
3. **Model egress is a decision.** Local, sanitized, derived-only, and remote
   processing are policy choices, never silent fallbacks.
4. **Capture is not truth.** Connected tools report observations; instance-
   owned mining decides what may become knowledge.
5. **Knowledge needs evidence.** Claims and relationships must lead back to
   inspectable sources and processing lineage.
6. **Collection must remain bounded.** Self-hosting is not permission to watch
   everything.
7. **Forgetting must be real.** Retention and deletion cannot remain permanent
   roadmap promises.
8. **Providers remain replaceable.** The user's memory should outlive any one
   model, vendor, or hosting arrangement.
9. **Limits stay visible.** Documentation must separate moral commitments,
   accepted design, and executable guarantees.

The architecture, policy, security, and lifecycle guides describe how these
principles map to present code and unfinished controls.
