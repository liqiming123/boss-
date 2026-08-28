export type Extraction<T>={status:'OK';value:T}|{status:'ERROR';errorCode:string};
export interface AccountData{displayName:string} export interface CandidateData{displayName:string;platformCandidateId?:string} export interface JobData{displayName:string}
export interface AdapterDiagnostics{platform:string;adapterVersion:string;pageType:string;accountStatus:string;candidateStatus:string;jobStatus:string;platformIdStatus:string;errorCodes:string[];sanitizedContext:Record<string,string|number|boolean>}
export interface RecruitmentSiteAdapter{readonly platform:string;canHandle(url:string):boolean;extractAccount():Promise<Extraction<AccountData>>;extractCandidate():Promise<Extraction<CandidateData>>;extractJob():Promise<Extraction<JobData>>;observePageChange(callback:()=>void):()=>void;getDiagnostics():Promise<AdapterDiagnostics>}

