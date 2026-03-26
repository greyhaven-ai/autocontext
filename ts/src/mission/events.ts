/**
 * Mission event emitter for dashboard streaming (AC-414).
 *
 * Emits typed events when mission state changes. WebSocket server
 * subscribes to these events and broadcasts to connected clients.
 */

import { EventEmitter } from "node:events";

export interface MissionCreatedEvent {
  missionId: string;
  name: string;
  goal: string;
  timestamp: string;
}

export interface MissionStepEvent {
  missionId: string;
  description: string;
  stepNumber: number;
  timestamp: string;
}

export interface MissionStatusChangedEvent {
  missionId: string;
  from: string;
  to: string;
  timestamp: string;
}

export interface MissionVerifiedEvent {
  missionId: string;
  passed: boolean;
  reason: string;
  timestamp: string;
}

export class MissionEventEmitter extends EventEmitter {
  emitCreated(missionId: string, name: string, goal: string): void {
    this.emit("mission_created", {
      missionId,
      name,
      goal,
      timestamp: new Date().toISOString(),
    } satisfies MissionCreatedEvent);
  }

  emitStep(missionId: string, description: string, stepNumber: number): void {
    this.emit("mission_step", {
      missionId,
      description,
      stepNumber,
      timestamp: new Date().toISOString(),
    } satisfies MissionStepEvent);
  }

  emitStatusChange(missionId: string, from: string, to: string): void {
    this.emit("mission_status_changed", {
      missionId,
      from,
      to,
      timestamp: new Date().toISOString(),
    } satisfies MissionStatusChangedEvent);
  }

  emitVerified(missionId: string, passed: boolean, reason: string): void {
    this.emit("mission_verified", {
      missionId,
      passed,
      reason,
      timestamp: new Date().toISOString(),
    } satisfies MissionVerifiedEvent);
  }
}
