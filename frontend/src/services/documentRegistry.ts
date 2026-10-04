export interface DocumentAssociation {
  part_number: string;
  revision: string | null;
  role: 'finished_drawing' | 'process_drawing' | 'cad' | 'quote';
  manufacturing_state: 'finished' | 'raw' | 'intermediate' | 'unresolved';
}

export interface DocumentRegistry {
  version: number;
  documents: { document_id: string; path: string; sha256: string; size_bytes: number;
    original_name: string; association: DocumentAssociation | null }[];
}
