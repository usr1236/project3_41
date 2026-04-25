# VitalTrack — Views and Viewpoints Mapping

This document maps every shipped artifact to the IEEE 42010 viewpoint(s) it addresses, the stakeholders it serves, and the architectural concerns it answers.

## Background: IEEE 42010 + Kruchten 4+1

VitalTrack adopts a viewpoint set inspired by Rozanski & Woods and augmented with the Use Case (Scenarios) viewpoint from Kruchten's 4+1 model. The C4 model (Brown) is used for structural/deployment diagrams; UML is used for class, sequence, activity, state, and use case diagrams.

## Diagram index

| # | File | Type | Viewpoint(s) | Stakeholder(s) |
|---|------|------|---|---|
| 01 | `01_c4_context.puml` | C4 L1 Context | Functional, Use Case | Patients, Doctors, Hospital Administrators, Development Team |
| 02 | `02_c4_container.puml` | C4 L2 Container | Functional, Deployment | System Administrators, Hospital Administrators, Operations Team, Development Team |
| 03 | `03_deployment_diagram.puml` | UML Deployment | Deployment, Operational | Operations Team, System Administrators |
| 04 | `04_usecase_diagram.puml` | UML Use Case (overview) | Use Case (Scenarios) | All actors |
| 05 | `05_usecase_monitoring_alerts.puml` | UML Use Case (detail) | Use Case, Process/Runtime | Doctors, On-Call Doctors, Caregivers, Admins |
| 06 | `06_class_diagram.puml` | UML Class | Logical, Information | Development Team |
| 07 | `07_sequence_vital_ingestion.puml` | UML Sequence | Process/Runtime | Doctors, Development Team, Operations |
| 08 | `08_sequence_escalation.puml` | UML Sequence | Process/Runtime | Clinical Team, Operations |
| 09 | `09_sequence_chatbot.puml` | UML Sequence | Process/Runtime, Functional | Patients, Development Team |
| 10 | `10_class_diagram_publish_subscribe.puml` | UML Class (DP-01) | Logical | Development Team |
| 11 | `11_class_diagram_strategy.puml` | UML Class (DP-02) | Logical | Development Team |
| 12 | `12_class_diagram_adapter.puml` | UML Class (DP-03) | Logical | Development Team |
| 13 | `13_class_diagram_factory.puml` | UML Class (DP-04) | Logical | Development Team |
| 14 | `14_class_diagram_observer.puml` | UML Class (DP-05) | Logical | Development Team |
| 15 | `15_access_matrix.md` | Tabular artifact | Security | Regulatory Stakeholders, Admins, Operations |
| 16 | `16_er_diagram.puml` | UML ER (entity-style) | Information | Regulatory Stakeholders, Development Team |
| 17 | `17_state_escalation_case.puml` | UML State Machine | Process/Runtime, Logical | Clinical Team, Development Team |
| 18 | `18_activity_vital_ingestion.puml` | UML Activity | Process/Runtime | Development Team, Operations |
| 19 | `19_activity_escalation.puml` | UML Activity | Process/Runtime | Clinical Team, Operations, Development Team |
| 20 | `20_c4_component_notification.puml` | C4 L3 Component | Logical, Functional | Development Team |
| 21 | `21_c4_component_ingestion.puml` | C4 L3 Component | Logical, Functional | Development Team |
| 22 | `22_c4_code_notification_chatbot.puml` | C4 L4 Code | Logical | Development Team |
| 23 | `23_c4_code_ingestion_pipeline.puml` | C4 L4 Code | Logical | Development Team |
| 24 | `24_views_viewpoints_mapping.md` | Tabular index | All viewpoints | All stakeholders |

## Viewpoint coverage

### Functional Viewpoint
**Concern**: External behavior and responsibilities of the system, including ingestion, monitoring, alerting, prediction, chatbot, administration.

**Artifacts**:
- `01_c4_context.puml`
- `02_c4_container.puml`
- `09_sequence_chatbot.puml`
- `20_c4_component_notification.puml`
- `21_c4_component_ingestion.puml`

### Logical Viewpoint
**Concern**: Internal decomposition into modules, subsystems, and relationships.

**Artifacts**:
- `06_class_diagram.puml`
- `10_class_diagram_publish_subscribe.puml`
- `11_class_diagram_strategy.puml`
- `12_class_diagram_adapter.puml`
- `13_class_diagram_factory.puml`
- `14_class_diagram_observer.puml`
- `17_state_escalation_case.puml`
- `20_c4_component_notification.puml`
- `21_c4_component_ingestion.puml`
- `22_c4_code_notification_chatbot.puml`
- `23_c4_code_ingestion_pipeline.puml`

### Information Viewpoint
**Concern**: Data storage, flow, and lifecycle for users, patients, vitals, alerts, notifications, predictions, and escalation.

**Artifacts**:
- `16_er_diagram.puml`
- `06_class_diagram.puml`

### Process / Runtime Viewpoint
**Concern**: Runtime interactions, including asynchronous event processing, retries, idempotency, WebSocket updates, escalation timing, and chatbot interaction flows.

**Artifacts**:
- `07_sequence_vital_ingestion.puml`
- `08_sequence_escalation.puml`
- `09_sequence_chatbot.puml`
- `17_state_escalation_case.puml`
- `18_activity_vital_ingestion.puml`
- `19_activity_escalation.puml`

### Deployment Viewpoint
**Concern**: Mapping of software to infrastructure (Docker Compose, gateway, services, broker, database, frontend).

**Artifacts**:
- `03_deployment_diagram.puml`
- `02_c4_container.puml`

### Security Viewpoint
**Concern**: JWT-based authentication, role-based authorization, credential hashing, and audit logging.

**Artifacts**:
- `15_access_matrix.md`
- security-relevant routes and checks in `07`, `08`, `09`
- auth/access components in `20_c4_component_notification.puml` and `21_c4_component_ingestion.puml`

### Operational Viewpoint
**Concern**: Monitoring, queue operations, and failure handling.

**Artifacts**:
- queue health + retry components in `20_c4_component_notification.puml`
- DLQ/event flow in `07_sequence_vital_ingestion.puml` and `10_class_diagram_publish_subscribe.puml`
- `03_deployment_diagram.puml`

### Use Case (Scenarios) Viewpoint
**Concern**: End-to-end actor interactions validating other views.

**Artifacts**:
- `04_usecase_diagram.puml`
- `05_usecase_monitoring_alerts.puml`

## C4 model levels for VitalTrack

| Level | Diagram | What it shows |
|---|---|---|
| **C4 L1 Context** | `01_c4_context.puml` | System boundary, actors, and external systems |
| **C4 L2 Container** | `02_c4_container.puml` | Runtime containers and inter-container communication |
| **C4 L3 Component** | `20_c4_component_notification.puml`, `21_c4_component_ingestion.puml` | Internal service decomposition |
| **C4 L4 Code** | `22_c4_code_notification_chatbot.puml`, `23_c4_code_ingestion_pipeline.puml` | Code-level realization of key components |

## IEEE 42010 alignment summary

- Stakeholders identified
- Concerns elicited
- Viewpoints selected
- Views provided
- Correspondences documented via this mapping
- Rationale captured in ADR set
