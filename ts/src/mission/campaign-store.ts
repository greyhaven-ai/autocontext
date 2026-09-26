import Database from "better-sqlite3";
import { enableWal } from "../storage/sqlite-wal.js";

import type {
  Campaign,
  CampaignBudget,
  CampaignMissionEntry,
  CampaignStatus,
} from "./campaign-contracts.js";
import {
  countCampaignMissions,
  hasCampaignMission,
  insertCampaignMissionRecord,
  listCampaignMissionEntries,
  removeCampaignMissionRecord,
} from "./campaign-membership-store-workflow.js";
import { createCampaignTables } from "./campaign-store-workflow.js";
import {
  createCampaignRecord,
  getCampaignRecord,
  listCampaignRecords,
  touchCampaignRecord,
  updateCampaignStatusRecord,
} from "./campaign-store-query-workflow.js";
import { generateCampaignId } from "./campaign-lifecycle-workflow.js";

export class CampaignStore {
  #db: Database.Database;

  constructor(dbPath: string) {
    this.#db = new Database(dbPath);
    enableWal(this.#db);
    this.#db.pragma("foreign_keys = ON");
    createCampaignTables(this.#db);
  }

  createCampaign(opts: {
    name: string;
    goal: string;
    budget?: CampaignBudget;
    metadata?: Record<string, unknown>;
  }): string {
    const id = generateCampaignId();
    createCampaignRecord(this.#db, id, opts);
    return id;
  }

  getCampaign(id: string): Campaign | null {
    return getCampaignRecord(this.#db, id);
  }

  listCampaigns(): Campaign[] {
    return listCampaignRecords(this.#db);
  }

  setStatus(campaignId: string, status: CampaignStatus): void {
    updateCampaignStatusRecord(this.#db, campaignId, status);
  }

  touchCampaign(campaignId: string): void {
    touchCampaignRecord(this.#db, campaignId);
  }

  addMission(
    campaignId: string,
    missionId: string,
    opts?: { priority?: number; dependsOn?: string[] },
  ): void {
    insertCampaignMissionRecord(
      this.#db,
      campaignId,
      missionId,
      opts,
      this.missionCount(campaignId),
    );
    this.touchCampaign(campaignId);
  }

  removeMission(campaignId: string, missionId: string): void {
    removeCampaignMissionRecord(this.#db, campaignId, missionId);
    this.touchCampaign(campaignId);
  }

  missionCount(campaignId: string): number {
    return countCampaignMissions(this.#db, campaignId);
  }

  hasMission(campaignId: string, missionId: string): boolean {
    return hasCampaignMission(this.#db, campaignId, missionId);
  }

  missions(campaignId: string): CampaignMissionEntry[] {
    return listCampaignMissionEntries(this.#db, campaignId);
  }

  close(): void {
    this.#db.close();
  }
}
