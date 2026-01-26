import type { PartSummary } from '../../services/types';
import './TotalsCards.css';

interface TotalsCardsProps {
  summary: PartSummary;
}

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

const fmt = (v: unknown, digits = 6) => (isNum(v) ? v.toFixed(digits) : '—');

const add2 = (a: unknown, b: unknown) => (isNum(a) && isNum(b) ? a + b : null);

function TotalsCards({ summary }: TotalsCardsProps) {
  const totals: any = (summary as any)?.totals ?? {};
  const units: any = (summary as any)?.units ?? {};

  // Units fallback (some summaries may not include `units`)
  const unitVolume = units?.volume ?? 'in³';
  const unitArea = units?.area ?? 'in²';

  // ✅ New schema keys (preferred), with fallback to old schema keys
  const totalVolume = totals?.total_volume_in3 ?? totals?.volume_in3 ?? null;

  const odArea = totals?.total_od_area_in2 ?? totals?.od_area_in2 ?? null;
  const idArea = totals?.total_id_area_in2 ?? totals?.id_area_in2 ?? null;

  const totalSurfaceArea =
    totals?.total_surface_area_in2 ?? add2(odArea, idArea); // fallback definition: OD + ID

  // Optional/legacy extras (render only if present)
  const planarRingArea = totals?.planar_ring_area_in2 ?? null;
  const endFacesStart = totals?.end_face_area_start_in2 ?? null;
  const endFacesEnd = totals?.end_face_area_end_in2 ?? null;
  const endFacesSum = add2(endFacesStart, endFacesEnd);
  const odShoulderArea = totals?.od_shoulder_area_in2 ?? null;
  const idShoulderArea = totals?.id_shoulder_area_in2 ?? null;

  const showPlanarBreakdown =
    isNum(planarRingArea) ||
    isNum(endFacesStart) ||
    isNum(endFacesEnd) ||
    isNum(odShoulderArea) ||
    isNum(idShoulderArea);

  return (
    <div className="totals-cards">
      <h3>Totals</h3>

      <div className="cards-grid">
        <div className="card">
          <div className="card-label">Total Volume</div>
          <div className="card-value">
            {fmt(totalVolume, 6)} {unitVolume}
          </div>
        </div>

        <div className="card">
          <div className="card-label">Total Surface Area</div>
          <div className="card-value">
            {fmt(totalSurfaceArea, 6)} {unitArea}
          </div>
        </div>

        <div className="card">
          <div className="card-label">OD Surface Area</div>
          <div className="card-value">
            {fmt(odArea, 6)} {unitArea}
          </div>
        </div>

        <div className="card">
          <div className="card-label">ID Surface Area</div>
          <div className="card-value">
            {fmt(idArea, 6)} {unitArea}
          </div>
        </div>

        {/* Optional legacy/extended metrics */}
        {showPlanarBreakdown && (
          <div className="card">
            <div className="card-label">Planar Ring Area</div>
            <div className="card-value">
              {fmt(planarRingArea, 6)} {unitArea}
            </div>

            <div className="card-breakdown">
              <div className="breakdown-item">
                <span>End faces:</span>
                <span>
                  {fmt(endFacesSum, 6)} {unitArea}
                </span>
              </div>

              <div className="breakdown-item">
                <span>OD shoulders:</span>
                <span>
                  {fmt(odShoulderArea, 6)} {unitArea}
                </span>
              </div>

              <div className="breakdown-item">
                <span>ID shoulders:</span>
                <span>
                  {fmt(idShoulderArea, 6)} {unitArea}
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default TotalsCards;
