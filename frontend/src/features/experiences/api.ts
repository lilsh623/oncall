import type { ApiClient } from "../../api/client";
import type {
  ExperienceCandidate,
  PublishedExperience,
  SkillCandidate,
  SkillVersion,
} from "./types";

export function getExperienceCandidates(client: ApiClient, status = ""): Promise<ExperienceCandidate[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return client.request(`/api/v1/experiences/candidates${query}`);
}

export function getPublishedExperiences(client: ApiClient): Promise<PublishedExperience[]> {
  return client.request("/api/v1/experiences");
}

export function publishExperience(client: ApiClient, candidateId: string): Promise<PublishedExperience> {
  return client.request(`/api/v1/experiences/candidates/${candidateId}/publish`, {
    method: "POST",
    body: JSON.stringify({ comment: null }),
  });
}

export function rejectExperience(client: ApiClient, candidateId: string): Promise<ExperienceCandidate> {
  return client.request(`/api/v1/experiences/candidates/${candidateId}/reject`, {
    method: "POST",
    body: JSON.stringify({ comment: null }),
  });
}

export function generateSkillCandidate(
  client: ApiClient,
  experienceId: string,
): Promise<SkillCandidate> {
  return client.request(`/api/v1/experiences/${experienceId}/skill-candidates`, {
    method: "POST",
    body: JSON.stringify({ comment: null }),
  });
}

export function getSkillCandidates(client: ApiClient): Promise<SkillCandidate[]> {
  return client.request("/api/v1/experiences/skill-candidates");
}

export function publishSkillCandidate(
  client: ApiClient,
  candidateId: string,
): Promise<SkillVersion> {
  return client.request(`/api/v1/experiences/skill-candidates/${candidateId}/publish`, {
    method: "POST",
    body: JSON.stringify({ comment: null }),
  });
}

export function rejectSkillCandidate(
  client: ApiClient,
  candidateId: string,
): Promise<SkillCandidate> {
  return client.request(`/api/v1/experiences/skill-candidates/${candidateId}/reject`, {
    method: "POST",
    body: JSON.stringify({ comment: null }),
  });
}

export function getLearnedSkills(client: ApiClient): Promise<SkillVersion[]> {
  return client.request("/api/v1/experiences/skills");
}
