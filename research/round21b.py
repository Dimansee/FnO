src=open("round21.py").read().split("# ------------------------------------------------ B.")[0]
exec(src.replace("print(\"\\nA. feature","pass #"))
print("\nFILTER TEST: skip trades on the weaker side of one feature")
OUT2={}
for k in ("prev_range_pct","bb_width","beyond_prev_hl","st15_ok","vix","rsi_aligned","cpr_width"):
    keep=[t for t,f in zip(CUR,F) if (math.isnan(f[k]) or H(k,f[k]))]
    OUT2[k]=summ(keep); print(f"keep only HIGH {k:16s}", OUT2[k], flush=True)
# combine the two most consistent
both_=[t for t,f in zip(CUR,F) if H("prev_range_pct",f["prev_range_pct"]) and f["st15_ok"]>=1]
OUT2["prev_range+st15"]=summ(both_); print("prev_range & st15_ok", OUT2["prev_range+st15"])
json.dump(OUT2,open("round21b.json","w"),default=str,indent=1)
