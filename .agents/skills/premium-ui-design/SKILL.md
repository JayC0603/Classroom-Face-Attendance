---
name: premium-ui-design
description: Use when designing or reviewing web app UI for a polished, premium look with clear usability, including Vietnamese requests for giao diện sang trọng, đẹp, dễ dùng, button placement, animation, dashboards, forms, or monitoring screens.
---

# Premium UI Design

Design interfaces that feel considered and high quality while keeping the user's next action obvious. A premium appearance comes from clear hierarchy, consistent spacing, legible type, responsive feedback, and restrained detail—not decoration added at the expense of function.

## Ground the design

- Inspect the actual page, shared styles, components, and available UI states before proposing a visual direction.
- Identify the user's main task, the information needed to complete it, and the current friction.
- Reuse the product's component system and design tokens. Extend them consistently instead of introducing a competing theme or unnecessary dependency.
- Preserve backend behavior, permissions, data meaning, and existing workflows unless the user asks to change them.
- For camera, face-recognition, enrollment, or operational monitoring screens, also follow [face-recognition-ui-engineering](../face-recognition-ui-engineering/SKILL.md) for domain-specific status and identity semantics.

## Establish hierarchy and actions

- Organize the screen around the task: clear page title, grouped sections, meaningful status, supporting details, and contextual actions.
- Give each task area one visually dominant primary action. Keep secondary actions quieter and place each action beside the resource or form it affects.
- Use destructive actions sparingly, label their consequence, and require confirmation when the action is hard to reverse.
- Make button labels describe outcomes with concise verbs. Use icon-only buttons only for familiar actions and always provide an accessible name and tooltip.
- Keep button size, alignment, spacing, hover, pressed, disabled, loading, and focus states consistent. Prevent duplicate submissions while an action is pending.
- Place validation and success feedback next to the field or action that caused it, with a clear recovery action when possible.

## Create a restrained premium visual system

- Use a deliberate palette with readable contrast, one clear accent, semantic status colors, and surfaces that separate content without excessive nested cards.
- Use whitespace and alignment to group related controls. Keep headings, labels, body copy, and metadata visually distinct.
- Prefer crisp typography, balanced proportions, subtle borders, and consistent radii. Use gradients, glow, blur, and shadows only when they reinforce hierarchy and fit the product's identity.
- Make controls feel intentional and comfortable to use. Inputs need visible labels; placeholders are examples, not labels.
- Support the actual language and data. In Vietnamese interfaces, keep natural Vietnamese labels and diacritics; handle long names, IDs, URLs, and errors without obscuring controls.

## Use motion with purpose

- Add animation when it communicates a state change, confirms an action, reveals related content, or guides attention.
- Keep transitions brief and subtle; prefer opacity and small transforms over large movement, parallax, or layout shifts.
- Animate only the affected element. Avoid decorative motion that competes with live data, camera video, or important controls.
- Respect `prefers-reduced-motion`; provide an equivalent state change without nonessential motion.
- Give asynchronous work clear pending feedback and a stable layout so animation does not move buttons away while the user is interacting.

## Check usability and quality

- Provide loading, empty, success, error, unavailable, and reconnecting states where the flow needs them. Do not present stale or placeholder values as live.
- Use semantic HTML, keyboard-operable controls, visible focus, accessible names, and text or icons in addition to color. Aim for WCAG AA contrast.
- Check the layout at a narrow phone width and a standard desktop width. Prevent page-wide horizontal scrolling and preserve reachable actions at both sizes.
- When browser access is available, inspect the real page and exercise its main interaction. A build or component test alone does not establish visual quality.
- Run relevant frontend tests and build/type checks after code changes. Report what was visually and functionally verified; distinguish it from checks that were unavailable.

## Quick review

Before finishing, confirm that:

- The primary task and next action are obvious without reading every section.
- Buttons are close to the object they affect and have clear importance and state.
- Motion communicates something useful and has a reduced-motion path.
- Text, controls, and feedback remain readable and reachable on mobile and desktop.
- The visual treatment is coherent with the product and does not alter domain meaning.

### Example: camera monitoring

Let the live camera view lead the page. Put “Bật camera” or “Tạm dừng” beside that view, show connection and model status in a compact labeled area, and keep secondary diagnostics collapsed or below the live task. Use a brief status transition when the camera connects; keep the video itself steady and honor reduced-motion settings.
