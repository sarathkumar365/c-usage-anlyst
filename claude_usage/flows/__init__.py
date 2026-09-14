"""One module per agent flow. Each flow receives resolved inputs and owns its side effects.

discover  -> find local Claude sources, cache them in state.json
collect   -> read transcripts into filtered usage
enroll    -> obtain/store collector credentials (enroll or register)
sync      -> collect + discover + build payload + upload + record state
preflight -> installer readiness checks
status    -> local health summary
"""
