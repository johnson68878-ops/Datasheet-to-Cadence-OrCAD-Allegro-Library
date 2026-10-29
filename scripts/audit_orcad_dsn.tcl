load {D:/Cadence/SPB_17.4/tools/bin/orDb_Dll_Tcl64.dll} DboTclWriteBasic

proc cs {value} { return [DboTclHelper_sMakeCString $value] }
proc cv {value} { return [DboTclHelper_sGetConstCharPtr $value] }

if {$argc < 2 || $argc > 3} {
    puts stderr {usage: tclsh audit_orcad_dsn.tcl input.dsn output.tsv}
    exit 2
}

set inputPath [file normalize [lindex $argv 0]]
set outputPath [file normalize [lindex $argv 1]]
set session [DboTclHelper_sCreateSession]
catch {DboSession -this $session}
set status [DboState]
set design [$session GetDesignAndSchematics [cs $inputPath] $status]
if {$design == "NULL"} {
    puts stderr "cannot open design: $inputPath"
    exit 3
}

set report [open $outputPath w]
puts $report "schematic\tpage\treference\tpackage\tpart_value\tpcb_footprint"
array set visited {}
array set exportedPackages {}
set pinReport {}
if {$argc == 3} {
    set pinReport [open [file normalize [lindex $argv 2]] w]
    puts $pinReport "package\tsection\tpin_number\tpin_name"
}

proc exportPackagePins {package status} {
    global pinReport exportedPackages
    if {$pinReport == {} || $package == "NULL"} { return }
    set name [cs {}]
    $package GetName $name
    set key [cv $name]
    if {[info exists exportedPackages($key)]} { return }
    set exportedPackages($key) 1
    set parts [$package NewPartsIter $status]
    set part [$parts NextPart $status]
    set section 0
    while {$part != "NULL"} {
        set device [$package GetDevice $section $status]
        set pins [$part NewLPinsIter $status]
        set pin [$pins NextPin $status]
        while {$pin != "NULL"} {
            set number [cs {}]
            set label [cs {}]
            $pin GetPinName $label
            $device GetPinNumber [$pin GetPinPosition $status] $number
            puts $pinReport "$key\t$section\t[cv $number]\t[cv $label]"
            set pin [$pins NextPin $status]
        }
        catch {delete_DboLibPartPinsIter $pins}
        incr section
        set part [$parts NextPart $status]
    }
    catch {delete_DboPackagePartsIter $parts}
}

proc visitSchematic {schematic reportName status} {
    upvar $reportName report
    set schematicName [cs {}]
    $schematic GetName $schematicName
    set schematicText [cv $schematicName]
    set pages [$schematic NewPagesIter $status]
    set page [$pages NextPage $status]
    while {$page != "NULL"} {
        set pageName [cs {}]
        $page GetName $pageName
        set pageText [cv $pageName]
        set instances [$page NewPartInstsIter $status]
        set instance [$instances NextPartInst $status]
        while {$instance != "NULL"} {
            set placed [DboPartInstToDboPlacedInst $instance]
            if {$placed != "NULL"} {
                set ref [cs {}]
                set value [cs {}]
                set footprint [cs {}]
                catch {$placed GetReferenceDesignator $ref}
                catch {$placed GetPartValue $value}
                catch {$placed GetPCBFootprint $footprint}
                set package [$placed GetPackage $status]
                set packageName [cs {}]
                if {$package != "NULL"} { catch {$package GetName $packageName} }
                exportPackagePins $package $status
                puts $report "$schematicText\t$pageText\t[cv $ref]\t[cv $packageName]\t[cv $value]\t[cv $footprint]"
            }
            set instance [$instances NextPartInst $status]
        }
        catch {delete_DboPagePartInstsIter $instances}
        set page [$pages NextPage $status]
    }
    catch {delete_DboSchematicPagesIter $pages}
}

set root [$design GetRoot $status]
set root [DboViewToDboSchematic $root]
if {$root != "NULL"} {
    set rootName [cs {}]
    $root GetName $rootName
    set visited([cv $rootName]) 1
    visitSchematic $root report $status
}

set views [$design NewViewsIter $status $::IterDefs_SCHEMATICS]
set view [$views NextView $status]
while {$view != "NULL"} {
    set schematic [DboViewToDboSchematic $view]
    if {$schematic != "NULL"} {
        set name [cs {}]
        $schematic GetName $name
        set nameText [cv $name]
        if {![info exists visited($nameText)]} {
            set visited($nameText) 1
            visitSchematic $schematic report $status
        }
    }
    set view [$views NextView $status]
}
catch {delete_DboLibViewsIter $views}
close $report
if {$pinReport != {}} { close $pinReport }
catch {$session RemoveDesign $design}
catch {DboTclHelper_sDeleteSession $session}
exit 0
