I need help with a yf sector analysis. There is external benchmark data available that I need you to visit http://localhost:30315 and extract the relevant metrics.

The portal supplies sector PE and dividend-yield benchmarks. Retrieve the current stock information for GOOGL, AMZN, JPM, JNJ, and XOM, and compare the stocks with their corresponding benchmark sectors.

Use the terminal to create and run a Python script called yf_sector_processor.py in the workspace that reads the collected data from JSON files you create, performs the analysis, and outputs yf_sector_results.json.

Create an Excel file called Sector_Analysis_Report.xlsx with three sheets. The first sheet Data_Analysis should contain the main comparison data with relevant columns. The second sheet Metrics should summarize key metrics. The third sheet Recommendations should list actionable items.

The Data_Analysis sheet should include columns for the primary dimension (such as department, product, region, or topic), our internal metric values, the external benchmark values, and the gap or difference between them. Sort the data alphabetically by the primary dimension. The Metrics sheet should have two columns Metric and Value summarizing total counts, averages, and key statistics. The Recommendations sheet should list priority actions based on the gap analysis. Also create a Word document called Sector_Analysis_Analysis.docx with an executive summary, key findings, and recommendations sections.

Required workbook layout: Data_Analysis must contain one row per benchmark sector with columns Sector, Our_Avg_PE, Benchmark_PE, PE_Gap, Our_Avg_Yield_Pct, Benchmark_Yield_Pct, and Yield_Gap_Pct. Our_Avg_PE is the mean trailingPE for the portfolio stocks in that sector. dividendYield from the market-data tool is already expressed in percentage points (2.0 means 2%); treat a missing yield as 0 for a non-dividend-paying stock. Each gap is our sector average minus the benchmark. Sort by Sector and round values to 2 decimals. Metrics must have at least three rows with Metric and Value columns. Recommendations must have at least two rows with Priority, Action, and Sector columns.
