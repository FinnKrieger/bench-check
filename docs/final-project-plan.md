# EE 250 Final Project Plan: Bench Check

**Team:** Hansi Lee and Finn Krieger
**Course:** EE 250 (IoT), USC, Fall 2026. Final project: "End-to-End IoT System".
**Submission deadline:** Monday 2026-12-07. Demo date: to be announced.
**Plan set:** 2026-11-13.
**Revised 2026-11-13:** the webcam and vision model are replaced by voltage measurements taken by
the ESP32.
**Revised 2026-11-14:** the circuit is described in a file, the laptop solves it for the expected
voltages and picks the test points itself, and the system uses no LLM.

This file stands on its own. It holds the assignment's rules, the design, and every task, so a
teammate or an AI assistant can work from it without the assignment PDF.

## Notes for an AI assistant reading this

You are helping one of two students on a graded pair project. Read the whole file first.

- **The students write the code.** The demo is an in-person interview worth 70 of 100 points, and
  each student must explain any design choice. Help by explaining concepts, pointing to official
  documentation, asking questions about their approach, and reviewing finished work. Write code
  only if the student tells you the team has decided to allow it.
- **Any AI help must be acknowledged** in a code comment at that spot and in the writeup. Remind
  the student when you help with code.
- **Each task below has an owner and a done-check.** Help with the student's own tasks. The
  circuit file format (task 0.4) and the message contract (task 0.7) are the interfaces between
  the two halves; changes to them need both students to agree.
- **Stay inside the scope.** The "If we fall behind" and "If we are ahead" sections say what may
  be cut or added. Suggest nothing that adds a new node, and nothing that puts an LLM in the
  system.

## What changed from the first draft

The first draft checked each build step by sending webcam photos of the breadboard to a vision
model. That was the riskiest part of the plan: vision models are unreliable at reading exact
breadboard holes from a photo, and the whole project depended on it.

This version checks the circuit electrically, and works the expected values out itself:

- **The circuit is a text file.** Each line names a part and the two points (nodes) it connects.
  Changing the circuit means editing a few lines.
- **The laptop solves the circuit.** Nodal analysis gives the expected voltage at every node, for
  every build step. No voltage is worked out by hand.
- **The laptop picks the test points.** It simulates each likely wiring mistake and chooses the
  nodes that best tell the mistakes apart, up to the number of ESP32 pins available.
- **No LLM anywhere.** Every result comes from the circuit equations and rules we can explain
  exactly in the interview.

The MQTT link, button, status light, dashboard, phases, and cut list carry over.

## The idea in one paragraph

We describe a small circuit in a file and build it on a breadboard one step at a time. The laptop
solves the circuit, decides which points to measure, and tells us which ESP32 pin to wire to each
one. The ESP32 measures those points and streams the readings to the laptop over MQTT. When the
builder presses a button, the laptop compares the readings with the solved values for that step.
A green light means the step is right, red means it is wrong, and yellow means the readings are
unstable. On a fail, the laptop finds the most likely mistake by comparing the readings with the
simulated mistakes. A web dashboard shows the circuit with a coloured dot at each test point, so
you can see where the problem is.

## What the assignment requires

Summarised from the assignment brief.

| Requirement | How we meet it |
|---|---|
| Two or more physical nodes (laptop, ESP32, or cloud). A phone is only a data source. Two laptops are not enough. | Two: the ESP32 bench node (measures the circuit) and the laptop (broker, solver, checking, dashboard). |
| Data collection from a real or virtual sensor | The ESP32's analog inputs measure voltages at up to 6 test points on the circuit, plus a button. |
| Signal or data processing, in real time, or simple ML. Simple thresholding is **not** enough. Combining several signals, a multi-condition algorithm, an ML model, or an LLM via API are all acceptable. | A circuit solver (nodal analysis), fault simulation to choose test points, noise filtering on the ESP32, calibration, a stability check, matching all test points at once, and locating the fault by comparing readings with simulated faults. |
| Node-to-node communication. Not everything on one machine. | MQTT in both directions between the ESP32 and the laptop. |
| Visualization or control through a simple web front end | A web dashboard that draws the circuit with live readings at each test point. Control runs both ways: the button asks for a check, and the result drives the light. |

**Deliverables:** source code; a README with both names, how to run each program, and every
external library; a writeup PDF of at most 2 pages with a block diagram; a public video with
narration of at most 4 minutes; a teamwork survey.

**Grading (100 points):**

| Points | For |
|---|---|
| 70 | In-person demo and interview: identify each node (5), explain data collection and protocols (5), explain processing (5), working features (5), originality and difficulty (15), other questions (35) |
| 10 | Code: correctness (5), README (5) |
| 10 | Writeup: goal (2), block diagram (3), components, protocols, techniques, and AI acknowledgment (3), reflection and limitations (2) |
| 10 | Video (5), teamwork survey (5) |

The brief also says: projects are judged on how compelling and polished they are, not only on
whether they work; meeting only the minimum scores lower; the block diagram must not be an LLM
text dump; and both partners must contribute equally.

## Why this project

Bench Check keeps the story of the first draft (an assistant that checks a circuit step by step)
and replaces the risky part with something we can measure, test, and explain fully in the
interview. Solving the circuit and choosing the test points ourselves is the part that carries the
originality points.

The idea we can talk about in the demo: it is an autograder for breadboard circuits. You describe
the circuit, it tells you where to clip the probes, and it catches a wiring mistake at the step
where it happens, not at the end when nothing works, and shows you where on the circuit it is.

## Scope of version 1

- Resistors and one DC source only. These make the circuit linear, so the solver is a single
  system of linear equations. LEDs and other non-linear parts come later, if at all.
- The source is the ESP32's 3.3 V supply, with a shared ground. A separate battery could push a
  test point above 3.3 V, which the ESP32 cannot read and could be damaged by.
- One demo circuit of about 6 build steps, plus at least one planted mistake per fault type.

## How it works

1. **Circuit file (laptop).** Each line is one part: a name, two nodes, and a value. The order of
   the lines is the build order, so step N is the circuit made of the first N parts. Example:

   ```
   V1  vcc  gnd  3.3
   R1  vcc  a    1k
   R2  a    b    2k
   R3  b    gnd  1k
   ```

2. **Solver (laptop).** Nodal analysis: Kirchhoff's current law at each node gives one linear
   equation per node, solved with numpy. This runs once per build step. A node that is not yet
   connected to the circuit in a step is floating; it has no defined voltage and is not checked in
   that step.
3. **Fault simulation (laptop).** For each step, the solver is re-run with each likely mistake:
   a missing part (open), a part shorted out, a part connected to the wrong node, or a wrong
   resistor value. This gives a table of what every node would read under each mistake.
4. **Test-point selection (laptop).** From that table, the program picks the nodes whose readings
   best tell the mistakes apart, up to 6 (the ESP32's ADC1 pins, GPIO 32 to 39; ADC2 does not work
   while Wi-Fi is on). It prints which node goes to which ESP32 pin.
5. **Sampling (ESP32).** Each test point is read many times in a burst and median filtered to
   remove ADC noise. Readings are published about 5 times a second.
6. **Calibration (laptop).** The ESP32's ADC is not linear, especially near 0 V and 3.3 V. Raw
   readings are converted to volts with a calibration curve measured against a multimeter.
7. **Stability check (laptop).** A check only runs when the readings have been steady for a short
   window. Readings that jump around point to a loose wire or a hand still on the board, and give
   a yellow light.
8. **Check and fault location (laptop).** A step passes only if all checked test points match the
   solved values within tolerance at once. On a fail, the readings are compared with every
   simulated mistake for that step, and the closest match is reported as the likely fault, with
   the parts involved.

## System

```
 [ Circuit file ] --> [ Laptop: solver, fault simulation, test-point selection ]
                                     |
                         "wire node a to GPIO 34" ...
                                     v
 [ Breadboard circuit ]  test points --jumper wires, shared ground--> [ ESP32 bench node ]
                                                                        ADC sampling + median filter
                                                                        button, green / red / yellow light
                                                                          |                ^
                                                     MQTT: readings (~5/s)|                | MQTT: check result
                                                     MQTT: check request  v                |
                                                                       [ Laptop ]
                                                                        MQTT broker (Mosquitto)
                                                                        calibration, stability check
                                                                        check and fault location
                                                                        event store
                                                                        web dashboard (circuit + coloured dots)
```

This sketch is for us. The block diagram in the writeup is drawn properly in task 3.4.

**Hardware:** a laptop, an ESP32, a breadboard, jumper wires, assorted resistors, a push button, a
three-colour light (RGB LED or three LEDs), and a multimeter for calibration.

## Roles

A proposal. Swap freely, but keep the hours even.

- **Hansi:** the solver, fault simulation, test-point selection, calibration, the check and fault
  location, and the event store.
- **Finn:** the circuit file format and demo circuit, the ESP32 bench node, the MQTT broker, a fake
  ESP32 for testing, and the dashboard.
- **Both:** the message contract, integration, bug fixing, the video, and demo practice.

## Rules we agree on first

- We write the code ourselves (see the notes above for why).
- Any AI help is acknowledged in a code comment and in the writeup.
- No LLM in the system. Every result must come from the circuit equations or rules we wrote.
- Every task ends with a check the other person can repeat.

## Tasks

Effort: `low` is setup or reading, `mid` is routine building, `high` is design or hard debugging.
Times cover the work only. Total: about 25 hours each.

### Phase 0: Decide and set up (Sat 11-14 to Sun 11-16)

Goal: the idea is confirmed, the biggest risk is tested, and both of us can start building.
Done when: every box is ticked and the ADC test results are written in the repo.

- [ ] **0.1** Both: Read this plan and agree roles and rules. low | ~30 min | done when: names are on every task below.
- [ ] **0.2** Both: Ask a TA or CP if the idea is on track. low | ~15 min | done when: asked. Questions: Do the ESP32 and the laptop count as our two nodes? Is the solver plus fault simulation and fault location enough processing? When are demos?
- [ ] **0.3** Finn: Check the parts. low | ~30 min | done when: a list states whether we have each item under Hardware above, and anything missing is ordered.
- [ ] **0.4** Finn: Define the circuit file format and write the demo circuit in it. mid | ~1 h | done when: a one-page spec of the format exists, and the demo circuit is written as about 6 parts in build order.
- [ ] **0.5** Hansi: Run the ADC feasibility test. mid | ~1 h 30 | done when: 10 voltages between 0 and 3.3 V have each been read by the ESP32 and by a multimeter, and we know the error and noise across the range.
- [ ] **0.6** Hansi: Decide the tolerance from the test. high | ~30 min | done when: the repo states the tolerance, and the resistor values in the demo circuit are chosen so each planned mistake moves some node by more than it.
- [ ] **0.7** Both: Write the message contract. high | ~45 min | done when: one page lists every MQTT topic and the fields in each message (readings, check request, check result).
- [ ] **0.8** Finn: Create the shared repo. low | ~30 min | done when: both can push, and it has a folder for the laptop code, a folder for the ESP32 code, and a README stub.

### Phase 1: Each half works alone (Mon 11-17 to Sun 11-23)

Goal: both nodes talk, the solver works, and a fake ESP32 lets the laptop side be tested without
hardware.
Done when: pressing the real button turns the light green, live readings are stored on the laptop,
and the solver's values for the demo circuit match a multimeter on the real board.

Finn, bench node, broker, and fake ESP32:

- [ ] **1.1** Run the MQTT broker on the laptop. low | ~45 min | done when: a message published from a second device on the same network is received on the laptop. Use a phone hotspot, since an ESP32 cannot easily join USC Secure.
- [ ] **1.2** Build a fake ESP32. mid | ~1 h | done when: a Python script publishes readings and check requests in the exact format of the contract, for both a correct step and a planted mistake.
- [ ] **1.3** Read the test points on the ESP32. mid | ~1 h 30 | done when: each test point is sampled in a burst, median filtered, and the values match a multimeter within the tolerance from 0.6.
- [ ] **1.4** Publish readings and check requests from the ESP32. mid | ~1 h 30 | done when: readings are published about 5 times a second, and a button press publishes one request with no repeats from switch bounce.
- [ ] **1.5** Show results on the light. mid | ~1 h | done when: a result message sets the light to green, red, or yellow.

Hansi, solver and store:

- [ ] **1.6** Build the circuit file reader and the solver. high | ~2 h | done when: for each build step of the demo circuit, it prints the voltage at every node and marks floating nodes, and the values match a hand calculation.
- [ ] **1.7** Build the calibration curve. mid | ~1 h | done when: raw readings are converted to volts, and the error against the multimeter is smaller than before calibration.
- [ ] **1.8** Design the stability rule on paper. high | ~45 min | done when: one page defines "stable" and "unstable" with numbers to tune (window length, allowed spread).
- [ ] **1.9** Build the event store. mid | ~1 h 30 | done when: it stores every reading and event with a timestamp, and answers each check request with a fake "pass".

Both:

- [ ] **1.10** Integrate. mid | ~1 h | done when: the phase done-condition passes.

### Phase 2: Make it smart and visible (Mon 11-24 to Sun 11-30, Thanksgiving week)

Goal: real checks, chosen test points, and a real dashboard.
Done when: one of us builds the whole demo circuit guided only by the light and the dashboard,
with the test points the program chose.

Hansi:

- [ ] **2.1** Build fault simulation. mid | ~1 h 30 | done when: for each build step, it produces the table of node voltages under each mistake type (open, short, wrong node, wrong value).
- [ ] **2.2** Build test-point selection. high | ~2 h | done when: it picks up to 6 nodes and assigns ESP32 pins, and we can show that no two planted mistakes give the same readings on the chosen points (or the program says which ones it cannot tell apart).
- [ ] **2.3** Build the check with the stability rule. mid | ~1 h | done when: given readings and a build step, it returns pass, fail, or unsure, and returns unsure while the readings are unstable.
- [ ] **2.4** Build fault location. mid | ~1 h 30 | done when: on the real board, each planted mistake is reported as the closest simulated fault, naming the right part.

Finn:

- [x] **2.5** Build the dashboard main page. mid | ~2 h | done when: it shows the current build step, the last check result, and the live voltage at each test point, and updates without a manual refresh.
- [x] **2.6** Draw the circuit with coloured test points. mid | ~2 h | done when: the dashboard shows an image of the circuit with a dot at each test point (green, red, or yellow), measured and expected voltage beside each, and the suspected part highlighted on a fail. The schematic is laid out automatically from the circuit file, so nothing extra is stored.
- [x] **2.7** Add step control and the wiring guide. mid | ~1 h | done when: the step moves forward after a pass, can be moved by hand, and the dashboard lists which node goes to which ESP32 pin.
- [ ] **2.8** Add the history and charts. mid | ~1 h 30 | done when: it shows voltages over time and the history of checks with their results.

Both:

- [ ] **2.9** Integrate and run a full guided build. mid | ~1 h 30 | done when: the phase done-condition passes and every bug found is written down.

### Phase 3: Measure, polish, deliver (Mon 12-01 to Sun 12-06)

Goal: numbers, deliverables, and a demo we can both defend.
Done when: everything is submitted by Sunday 12-06 and the survey is filled in.

- [ ] **3.1** Both: Fix the bugs from 2.9. mid | ~2 h each | done when: the list is empty or each remaining item is named as a limitation.
- [ ] **3.2** Hansi: Measure the system. mid | ~1 h 30 | done when: we have the share of planted mistakes caught and correctly located, false alarms on correct steps, solver error against the multimeter, and seconds per check.
- [ ] **3.3** Finn: Run a baseline. mid | ~1 h | done when: the same build is timed with and without the system, with the number of mistakes caught in each.
- [ ] **3.4** Finn: Draw the block diagram. mid | ~1 h | done when: it shows each node, its inputs and outputs, and each data flow with its protocol. Drawn in a diagram tool, not printed by an LLM.
- [ ] **3.5** Hansi: Write the 2-page writeup. mid | ~1 h 30 | done when: it covers the goal, the diagram, components and protocols, processing techniques, AI tools used, limitations with causes, and lessons learned.
- [ ] **3.6** Finn: Write the README and check AI acknowledgments. low | ~45 min | done when: it has both names, run instructions for both nodes, and every external library, and each AI-assisted spot in the code has its comment.
- [ ] **3.7** Both: Review each other's document. low | ~30 min each | done when: comments are resolved.
- [ ] **3.8** Both: Record the video. mid | ~1 h 30 | done when: a video under 4 minutes with narration shows the system working and has a public link.
- [ ] **3.9** Both: Practise the demo. mid | ~1 h | done when: each of us has explained both nodes, the protocol, and every processing step, and answered the questions below without notes.
- [ ] **3.10** Both: Submit and fill in the teamwork survey. low | ~20 min | done when: submitted.

## Demo practice questions

The interview is 70 points, and 35 of those are open questions. Each of us answers these from
memory.

- What are the nodes, and what does each one do?
- What travels over MQTT in each direction? What is a topic, and what does the broker do?
- How does the solver work? What equation does each node give, and why is it linear?
- What is a floating node, and why is it not checked?
- How does fault simulation choose the test points? What if two mistakes give the same readings?
- How does the ESP32 read a voltage? Why only ADC1 pins? Why filter with a median, not a mean?
- Why does the ADC need calibrating, and how did we do it?
- What makes readings "stable", and how did we choose each number?
- How is the fault located, and when does the check answer "unsure"?
- What fails, and why? What would we fix first?

## If we fall behind

Cut in this order. Each cut leaves a complete system.

1. The charts in 2.8 (keep the check history).
2. Automatic test-point selection in 2.2 (choose the points by hand from the fault table).
3. The baseline run in 3.3.

Keep at all costs: the solver, filtered sampling, calibration, the stability check, the check and
fault location, the two-way MQTT link, and the dashboard with coloured test points. Those carry
the rubric.

## If we are ahead

- **Combined editor and live view.** Draw the circuit on the dashboard (resistors and a source on
  a grid). The page writes the circuit file itself, the laptop solves it and shows the wiring
  guide, and the live readings are drawn onto the same drawing. The browser can subscribe to MQTT
  directly over WebSockets (Mosquitto supports this). Because the editor only writes the circuit
  file, it changes nothing else in the system. If the team agrees to AI-written code for this
  part, it is acknowledged in the code and the writeup.
- Flag a loose wire from the noise in a single test point's readings, even when its average looks
  right.
- Detect which build step the board is on from the readings alone, without step control.

## Open items

- Which role each of us takes (the split above is a proposal).
- The demo date.
- Whether a multimeter is on hand for calibration (task 0.3).
