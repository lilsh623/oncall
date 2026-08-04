import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import {
  getExperienceCandidates,
  getPublishedExperiences,
  generateSkillCandidate,
  getLearnedSkills,
  getSkillCandidates,
  publishSkillCandidate,
  publishExperience,
  rejectSkillCandidate,
  rejectExperience,
} from "../features/experiences/api";
import type { ExperienceCandidate } from "../features/experiences/types";

function CandidateDetail({ candidate, canReview, onPublish, onReject }: {
  candidate: ExperienceCandidate;
  canReview: boolean;
  onPublish: () => void;
  onReject: () => void;
}) {
  return <section><h2>{candidate.title}</h2><dl className="facts"><div><dt>状态</dt><dd>{candidate.status}</dd></div><div><dt>服务</dt><dd>{candidate.project_id} / {candidate.environment} / {candidate.service}</dd></div><div><dt>置信度</dt><dd>{candidate.confidence.toFixed(2)}</dd></div><div><dt>脱敏版本</dt><dd>{candidate.redaction_version}</dd></div></dl><h3>根因模式</h3><p>{candidate.root_cause}</p><h3>症状</h3><ul>{candidate.symptoms.map((item, index) => <li key={index}>{String(item)}</li>)}</ul><h3>已验证动作</h3><pre>{JSON.stringify(candidate.action, null, 2)}</pre><h3>恢复验证</h3><pre>{JSON.stringify(candidate.verification, null, 2)}</pre><p>来源 Incident：<Link to={`/incidents/${candidate.incident_id}`}>{candidate.incident_id}</Link></p><p><code>{candidate.content_hash}</code></p>{candidate.duplicate_of_id && <p className="error">检测到重复模式：{candidate.duplicate_of_id}</p>}{canReview && candidate.status === "PENDING_REVIEW" && <p className="actions"><button disabled={Boolean(candidate.duplicate_of_id)} onClick={onPublish}>审核并发布</button><button className="danger" onClick={onReject}>拒绝候选</button></p>}</section>;
}

export function ExperiencePage() {
  const { client, user } = useAuth();
  const cache = useQueryClient();
  const [status, setStatus] = useState("PENDING_REVIEW");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const candidates = useQuery({ queryKey: ["experience-candidates", status], queryFn: () => getExperienceCandidates(client, status) });
  const published = useQuery({ queryKey: ["published-experiences"], queryFn: () => getPublishedExperiences(client) });
  const skillCandidates = useQuery({ queryKey: ["skill-candidates"], queryFn: () => getSkillCandidates(client) });
  const learnedSkills = useQuery({ queryKey: ["learned-skills"], queryFn: () => getLearnedSkills(client) });
  const refresh = async () => { await Promise.all([cache.invalidateQueries({ queryKey: ["experience-candidates"] }), cache.invalidateQueries({ queryKey: ["published-experiences"] }), cache.invalidateQueries({ queryKey: ["skill-candidates"] }), cache.invalidateQueries({ queryKey: ["learned-skills"] })]); };
  const publication = useMutation({ mutationFn: (id: string) => publishExperience(client, id), onSuccess: refresh });
  const rejection = useMutation({ mutationFn: (id: string) => rejectExperience(client, id), onSuccess: refresh });
  const generation = useMutation({ mutationFn: (id: string) => generateSkillCandidate(client, id), onSuccess: refresh });
  const skillPublication = useMutation({ mutationFn: (id: string) => publishSkillCandidate(client, id), onSuccess: refresh });
  const skillRejection = useMutation({ mutationFn: (id: string) => rejectSkillCandidate(client, id), onSuccess: refresh });
  const selected = candidates.data?.find((item) => item.id === selectedId) ?? candidates.data?.[0];
  return <main><h1>经验中心</h1><p>只有恢复验证通过的 Incident 才会产生候选；经验和生成的 Skill 都必须由管理员分别审核。</p><p className="inline"><select value={status} onChange={(event) => { setStatus(event.target.value); setSelectedId(null); }}><option value="">全部候选</option><option value="PENDING_REVIEW">待审核</option><option value="PUBLISHED">已发布</option><option value="REJECTED">已拒绝</option></select></p>{candidates.isLoading && <p>正在加载候选…</p>}{candidates.error && <p className="error">{candidates.error.message}</p>}<table><thead><tr><th>候选</th><th>服务</th><th>状态</th><th>重复</th><th>创建时间</th></tr></thead><tbody>{candidates.data?.map((item) => <tr key={item.id}><td><button className="link-button" onClick={() => setSelectedId(item.id)}>{item.title}</button></td><td>{item.service}</td><td>{item.status}</td><td>{item.duplicate_of_id ? "是" : "否"}</td><td>{new Date(item.created_at).toLocaleString()}</td></tr>)}</tbody></table>{selected && <CandidateDetail candidate={selected} canReview={user?.role === "admin"} onPublish={() => publication.mutate(selected.id)} onReject={() => rejection.mutate(selected.id)} />}{(publication.error || rejection.error || generation.error || skillPublication.error || skillRejection.error) && <p className="error">{String(publication.error ?? rejection.error ?? generation.error ?? skillPublication.error ?? skillRejection.error)}</p>}<section><h2>已发布经验</h2>{published.isLoading && <p>正在加载…</p>}<ul>{published.data?.map((item) => <li key={item.id}>{item.title} · v{item.version} · {item.service}{user?.role === "admin" && <button onClick={() => generation.mutate(item.id)}>生成 Skill 候选</button>}</li>)}</ul></section><section><h2>Skill 候选</h2>{skillCandidates.isLoading && <p>正在加载…</p>}<table><thead><tr><th>Skill</th><th>版本</th><th>状态</th><th>来源经验</th><th>操作</th></tr></thead><tbody>{skillCandidates.data?.map((item) => <tr key={item.id}><td>{item.skill_name}</td><td>{item.proposed_version}</td><td>{item.status}</td><td>{item.experience_id.slice(0, 8)}</td><td>{user?.role === "admin" && item.status === "PENDING_REVIEW" && <span className="actions"><button onClick={() => skillPublication.mutate(item.id)}>发布 Skill</button><button className="danger" onClick={() => skillRejection.mutate(item.id)}>拒绝</button></span>}</td></tr>)}</tbody></table></section><section><h2>生效中的 Learned Skill</h2><ul>{learnedSkills.data?.map((item) => <li key={item.id}><strong>{item.skill_name}@{item.version}</strong> · {item.service} · {item.status}</li>)}</ul></section></main>;
}
