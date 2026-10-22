// Every engineering request captures a context generation. Superseded results
// are discarded, including errors, before they can update a newer project.
let generation = 0;
const client = crypto.randomUUID();
let reach = 0;
let model = "";
export const setContextReach = (value: number) => { reach = value; };
export const setContextModel = (value: string) => { model = value; };
export const contextModel = () => model;
export const contextHeaders = () => ({ "X-Rasplot-Context": `${client}:${generation}`, "X-Rasplot-Reach": String(reach), "X-Rasplot-Model": model });
export const contextVersion = () => generation;
export const advanceContext = () => ++generation;
export class StaleResponse extends Error {}
export function assertContext(version: number) {
  if (version !== generation) throw new StaleResponse("Superseded request");
}
