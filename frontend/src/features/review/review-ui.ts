export interface ReviewUi {transactionId: string; rejectDocumentValid: string; rejectReason: string; technicalOpen: boolean; rejectOpen: boolean;}
let recent: ReviewUi | undefined;
export function reviewUi(transactionId: string, reason: string): ReviewUi {
  if (recent?.transactionId !== transactionId) recent = {transactionId,rejectDocumentValid:'',rejectReason:reason,technicalOpen:false,rejectOpen:false};
  return recent;
}
