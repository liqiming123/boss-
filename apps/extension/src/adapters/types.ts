export type Extraction<T> =
  { status: "OK"; value: T } | { status: "ERROR"; errorCode: string };
export interface StatusEvidence {
  status: string;
  evidence: string;
  ruleVersion: string;
  observedAt: string;
}
export interface NativeCommunicationRecord {
  recruiterName: string;
  jobName: string;
  contactedAt: string;
  source: "BOSS_NATIVE";
}
export interface AccountData {
  displayName: string;
}
export interface CandidateData {
  displayName: string;
  age?: number;
  experience?: string;
  education?: string;
  platformCandidateId?: string;
  conversationStartedAt?: string;
  conversationUpdatedAt?: string;
  hasRecruiterOutbound?: boolean;
  statusEvidence?: StatusEvidence;
  historicalJobs?: string[];
  nativeCommunications?: NativeCommunicationRecord[];
  resumeStatus?: string;
  resumeDownload?: { url: string; fileName: string };
}
export interface JobData {
  displayName: string;
}
export interface AdapterDiagnostics {
  platform: string;
  adapterVersion: string;
  pageType: string;
  accountStatus: string;
  candidateStatus: string;
  jobStatus: string;
  platformIdStatus: string;
  errorCodes: string[];
  sanitizedContext: Record<string, string | number | boolean>;
}
export interface InterviewDetails {
  interview_type?: "ONLINE" | "OFFLINE";
  /** ISO-8601 with the +08:00 offset; only set when BOSS showed both a date and
   * a start time. */
  scheduled_at?: string;
  location?: string;
}
export interface RecruiterMessageSent {
  sentAt: string;
  evidence: "DELIVERY_MARKER" | "OUTGOING_TEXT";
  messageText?: string;
  statusEvidence?: StatusEvidence;
  /** Interview scheduler details, present only for a confirmed invitation. */
  interview?: InterviewDetails;
}
export interface ResumePreviewOpened {
  url?: string;
  fileName?: string;
  target?: HTMLElement;
}
export interface RecruitmentSiteAdapter {
  readonly platform: string;
  canHandle(url: string): boolean;
  isCandidateConversationPage(): boolean;
  extractAccount(): Promise<Extraction<AccountData>>;
  extractCandidate(): Promise<Extraction<CandidateData>>;
  extractJob(): Promise<Extraction<JobData>>;
  observePageChange(callback: () => void): () => void;
  observeRecruiterMessageSent(
    callback: (event: RecruiterMessageSent) => void,
  ): () => void;
  observeResumePreviewOpened?(
    callback: (event: ResumePreviewOpened) => void,
  ): () => void;
  getDiagnostics(): Promise<AdapterDiagnostics>;
}
