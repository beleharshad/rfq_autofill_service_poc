import type { PartSummary } from '../../services/types';
import './TotalsCards.css';

interface TotalsCardsProps {
  summary: PartSummary;
}

function TotalsCards({ summary }: TotalsCardsProps) {
  const { totals } = summary;

  return (
    <div className="totals-cards">
      <h3>Totals</h3>
      <div className="cards-grid">
        <div className="card">
          <div className="card-label">Total Volume</div>
          <div className="card-value">
            {totals.volume_in3.toFixed(6)} {summary.units.volume}
          </div>
        </div>

        <div className="card">
          <div className="card-label">Total Surface Area</div>
          <div className="card-value">
            {totals.total_surface_area_in2.toFixed(6)} {summary.units.area}
          </div>
        </div>

        <div className="card">
          <div className="card-label">OD Cylindrical Area</div>
          <div className="card-value">
            {totals.od_area_in2.toFixed(6)} {summary.units.area}
          </div>
        </div>

        <div className="card">
          <div className="card-label">ID Cylindrical Area</div>
          <div className="card-value">
            {totals.id_area_in2.toFixed(6)} {summary.units.area}
          </div>
        </div>

        <div className="card">
          <div className="card-label">Planar Ring Area</div>
          <div className="card-value">
            {totals.planar_ring_area_in2.toFixed(6)} {summary.units.area}
          </div>
          <div className="card-breakdown">
            <div className="breakdown-item">
              <span>End faces:</span>
              <span>
                {(totals.end_face_area_start_in2 + totals.end_face_area_end_in2).toFixed(6)}{' '}
                {summary.units.area}
              </span>
            </div>
            <div className="breakdown-item">
              <span>OD shoulders:</span>
              <span>
                {totals.od_shoulder_area_in2.toFixed(6)} {summary.units.area}
              </span>
            </div>
            <div className="breakdown-item">
              <span>ID shoulders:</span>
              <span>
                {totals.id_shoulder_area_in2.toFixed(6)} {summary.units.area}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default TotalsCards;





