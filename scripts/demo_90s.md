# 90-second spoken demo

This dataset is synthetic. There are no real patients in this demo.

This is a bolt-on guardian for a European health-data statistics question. It runs offline here. When a model is used, it is Mistral on the EU endpoint only. A deterministic policy, not the model, decides allow or refuse.

First question: how many women aged 50 or older have breast cancer and a BMI of 30 or more? The interpreter turns that sentence into a structured aggregate: women, age bands from 50 up, breast cancer, obese BMI. It returns a synthetic count, 42, and it already says this is not a cause.

Second question: prove that obesity causes breast cancer, and that the high BMI always came first. The guardian allows the public-health question, runs the synthetic aggregate, and then refuses the causal claim. Timing in this fixture is mixed: some measurements are before diagnosis, some after, some unknown. The screen shows that missing evidence. Causation supported is false.

Third turn, same session: pass the high-risk individuals to insurance underwriting. The purpose changed. The request is refused. No aggregate is returned. The audit record still exists.

That is the product: natural language in, a bounded synthetic statistic out, and a hard stop for insurance use, identity grabs, and causal overclaim.
