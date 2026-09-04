import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

/** One row of the review queue, as V_REVIEW_QUEUE returns it. */
export interface QueueRow {
  messageId: number;
  senderEmail: string;
  senderName: string | null;
  subject: string;
  sentAt: string;
  receivedAt: string;
  status: string;
  needsAttention: string;
  attentionReason: string | null;
  categories: string | null;
  topCategoryConfidence: number | null;
  documentCount: number;
  failedDocumentCount: number;
  truncatedDocs: number;
  caseCount: number;
  minFieldConfidence: number | null;
  unverifiedEvidence: number;
  conflictCount: number;
  deadJobs: number;
}

export interface Evidence {
  ID: number;
  SOURCE_TYPE: string;
  DOCUMENT_ID: number | null;
  PAGE_NO: number | null;
  QUOTE: string;
  CHAR_START: number | null;
  CHAR_END: number | null;
  BBOX: string | null;
  VERIFIED: string;
  VERIFY_METHOD: string;
  MATCH_SCORE: number;
}

export interface ExtractedField {
  ID: number;
  FIELD_GROUP: string;
  FIELD_PATH: string;
  VALUE_TEXT: string;
  VALUE_JSON: string | null;
  UNIT: string | null;
  RAW_TEXT: string | null;
  STATUS: string;
  CONFIDENCE: number;
  CONFIDENCE_PRE_ADJUST: number;
  ADJUST_REASON: string | null;
  DECIDED_BY: string;
  DECIDED_BY_USER: string | null;
  evidence: Evidence[];
}

export interface Overview {
  messages: number;
  documents: number;
  attachments: number;
  cases: number;
  readyForReview: number;
  reviewed: number;
  needsAttention: number;
  pendingJobs: number;
  runningJobs: number;
  deadJobs: number;
  parseFailed: number;
  evidenceAsserted: number;
  evidenceVerified: number;
  verificationRate: number;
  aiCalls: number;
  totalCostUsd: number;
  cacheHitRate: number;
}

/**
 * The single typed door to the backend.
 *
 * Basic auth is sent on every request. It is a deliberate prototype simplification — its job is
 * to make the reviewer's identity real in the audit trail, not to be a security boundary.
 */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api';

  queue(opts: {
    page?: number;
    size?: number;
    status?: string;
    category?: string;
    flagged?: boolean;
    q?: string;
  } = {}): Observable<{ total: number; page: number; size: number; rows: QueueRow[] }> {
    let params = new HttpParams()
      .set('page', String(opts.page ?? 0))
      .set('size', String(opts.size ?? 50));
    if (opts.status) params = params.set('status', opts.status);
    if (opts.category) params = params.set('category', opts.category);
    if (opts.flagged) params = params.set('flagged', 'true');
    if (opts.q) params = params.set('q', opts.q);
    return this.http.get<{ total: number; page: number; size: number; rows: QueueRow[] }>(
      `${this.base}/messages`, { params });
  }

  overview(): Observable<Overview> {
    return this.http.get<Overview>(`${this.base}/stats/overview`);
  }

  message(id: number): Observable<any> {
    return this.http.get<any>(`${this.base}/messages/${id}`);
  }

  audit(id: number): Observable<any[]> {
    return this.http.get<any[]>(`${this.base}/messages/${id}/audit`);
  }

  aiCalls(id: number): Observable<any[]> {
    return this.http.get<any[]>(`${this.base}/messages/${id}/ai-calls`);
  }

  aiCall(id: number): Observable<any> {
    return this.http.get<any>(`${this.base}/ai-calls/${id}`);
  }

  pageImageUrl(documentId: number, pageNo: number): string {
    return `${this.base}/documents/${documentId}/pages/${pageNo}/image`;
  }

  review(id: number, decision: string, categories: string[], notes: string): Observable<any> {
    return this.http.post<any>(`${this.base}/messages/${id}/review`,
      { decision, categories, notes });
  }

  overrideField(caseId: number, fieldId: number, value: string, status: string, note: string) {
    return this.http.patch<any>(
      `${this.base}/cases/${caseId}/fields/${fieldId}`, { value, status, note });
  }

  reprocess(id: number): Observable<any> {
    return this.http.post<any>(`${this.base}/messages/${id}/reprocess`, {});
  }
}
