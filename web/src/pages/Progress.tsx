import { Loading } from "../components";
import { useWorkout } from "../workout";
import { Records, useHomeData } from "./Home";

/** Records and milestones: power bests, yearly goal and the FTP trend. */
export function Progress() {
  const { sport, minutes } = useWorkout();
  const { data, error, loading } = useHomeData(minutes, sport);
  if (!data) return <Loading error={error} loading={loading} />;
  return <Records d={data} />;
}
