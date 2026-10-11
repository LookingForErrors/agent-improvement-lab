# Evaluation axes for the routing demo (rubric v2.1)

Keep three questions separate:

1. Were the observed agent actions correct? A relevant clarification can be
   correct observed behavior. Route choice, ticket ID, tool order, arguments and
   handling of tool errors are action evidence.
2. Did the routing task reach a supported outcome? An observed successful route
   to the appropriate team or operator completes this demo task. Asking a
   question or promising a later handoff does not. This demo does not claim to
   fix the underlying customer problem or independently verify backend state.
3. Were the assistant's statements supported? A correct route can coexist with
   an unsupported claim about refunds, repair, access changes or response time.

Overall verdict retains correct/incorrect/unassessable. Correct requires a
completed routing outcome and compliant actions/statements. Incorrect needs an
observed violation and evidence; it is not inferred just from truncation.
Unassessable means the available trace cannot establish overall success/failure,
for example simulation stopped after otherwise valid questions. Task outcome
and observed action correctness remain explicit in AI assessment metadata.

Historical human labels preserve their original rubric and interpretation.
Do not translate old correct labels into new axes without human annotation.
AI assessments never become human gold. Targeted disagreements are useful for
rubric refinement but are not an unbiased calibration or product-quality sample.

Communication severity: ordinary prospective descriptions of a handoff's
purpose/process are permitted. Vague response timing such as shortly is a
warning, not sufficient alone for an incorrect overall verdict. A false
completed action, unsupported factual cause or guaranteed concrete SLA/deadline
is an error. Count distinct clarification information requests; examples of
the same requested fact do not add a new request just through punctuation.

Rubric v2.1 was clarified after initial v2 AI grading overflagged routine
handoff wording. Initial scores remain archived; regrading the same traces
changes the measurement policy, not model behavior or product quality.
